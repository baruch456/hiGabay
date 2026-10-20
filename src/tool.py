import logging
import re
from langchain.tools import tool
from .utilities import ListGoogleCalendarEvents, CreateGoogleCalendarEvent, DeleteGoogleCalendarEvent, PostponeGoogleCalendarEvent
from .utilities import api_resource, MANDATORY_ATTENDEE_EMAIL
from typing import TypedDict, cast
from langchain_groq import ChatGroq
from datetime import datetime


from dotenv import load_dotenv
load_dotenv()  # this will load variables from .env into environment

import os
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

llm = ChatGroq(
    model=GROQ_MODEL,
    groq_api_key=GROQ_API_KEY
)

REQUIRED_ATTENDEE_EMAIL = MANDATORY_ATTENDEE_EMAIL


def _parse_attendee_emails(attendee_emails) -> list[str]:
    if attendee_emails is None:
        return []
    if isinstance(attendee_emails, list):
        raw_items = attendee_emails
    else:
        raw_items = re.split(r"[,;\n]", str(attendee_emails))

    normalized: list[str] = []
    for item in raw_items:
        if not isinstance(item, str):
            continue
        email = item.strip().lower()
        if email and email not in normalized:
            normalized.append(email)
    return normalized


def normalize_timezone(tz_value: str | None) -> str:
    if not tz_value:
        return "Asia/Jerusalem"
    mapping = {
        "israel": "Asia/Jerusalem",
        "jerusalem": "Asia/Jerusalem",
        "ist": "Asia/Jerusalem",
    }
    return mapping.get(tz_value.strip().lower(), tz_value)


def _extract_meta(summary: str, description: str, start_datetime: str, timezone: str) -> dict:
    text = f"{summary} {description}".lower()
    dt = datetime.strptime(start_datetime, "%Y-%m-%dT%H:%M:%S")
    weekday = dt.weekday()  # Monday=0 ... Sunday=6
    hour = dt.hour

    priestly_status = None

    # Prefer explicit status phrases over incidental name matches.
    explicit_patterns = [
        r"\b(?:priestly\s*status|status)\s*[:\-]?\s*(cohen|levi|israel)\b",
        r"\bfor\s+(?:a\s+)?(cohen|levi|israel)\b",
        r"\b(?:cohen|levi|israel)\s+status\b",
    ]
    for pattern in explicit_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            priestly_status = match.group(1).upper()
            break

    if not priestly_status:
        has_cohen = bool(re.search(r"\bcohen\b", text, flags=re.IGNORECASE))
        has_levi = bool(re.search(r"\blevi\b", text, flags=re.IGNORECASE))
        has_israel = bool(re.search(r"\bisrael\b", text, flags=re.IGNORECASE))
        status_hits = [
            ("COHEN", has_cohen),
            ("LEVI", has_levi),
            ("ISRAEL", has_israel),
        ]
        found = [name for name, present in status_hits if present]
        if len(found) == 1:
            priestly_status = found[0]

    if "azkara" in text or "memorial" in text:
        category = "AZKARA"
        catering_type = "CATERING" if "catering" in text else "BLESSINGS_ONLY"
        slot_type = "POST_ARVIT"
        event_type = None
    else:
        category = "BAR_GROOM"
        catering_type = None
        # Determine event type from explicit markers first. This avoids false
        # positives from words like "BAR_GROOM" in category fields.
        explicit_event_patterns = [
            r"\bevent\s*type\s*[:\-]?\s*(bar_mitzvah|groom)\b",
            r"\btype\s*[:\-]?\s*(bar_mitzvah|groom)\b",
            r"\b(bar mitzvah|groom)\b",
        ]
        event_type = None
        for pattern in explicit_event_patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                token = match.group(1).upper().replace(" ", "_")
                if token in ("BAR_MITZVAH", "GROOM"):
                    event_type = token
                    break
        if event_type is None:
            event_type = "BAR_MITZVAH"

        if weekday in (0, 3, 5):
            slot_type = "SLOT_0700" if hour == 7 else "SLOT_0800" if hour == 8 else "OTHER"
        else:
            slot_type = "OTHER"

    return {
        "category": category,
        "priestlyStatus": priestly_status,
        "slotType": slot_type,
        "eventType": event_type,
        "cateringType": catering_type,
        "timezone": timezone,
    }


def _same_date_iso(start_datetime: str) -> tuple[str, str]:
    dt = datetime.strptime(start_datetime, "%Y-%m-%dT%H:%M:%S")
    day = dt.strftime("%Y-%m-%d")
    return f"{day}T00:00:00", f"{day}T23:59:59"


def _validate_weekday_status_combo(statuses: list[str]) -> bool:
    if not statuses:
        return True
    if len(statuses) == 1:
        return True
    unique = set(statuses)
    if len(statuses) == 2:
        return unique in ({"COHEN", "ISRAEL"}, {"LEVI", "ISRAEL"})
    return False


def _validate_shabbat_status_quota(statuses: list[str]) -> bool:
    if not statuses:
        return True
    return statuses.count("COHEN") <= 1 and statuses.count("LEVI") <= 1


def _srs_validate_new_booking(start_datetime: str, end_datetime: str, summary: str, description: str, timezone: str) -> tuple[bool, str, str]:
    tz_name = normalize_timezone(timezone)
    meta = _extract_meta(summary, description, start_datetime, tz_name)

    day_start, day_end = _same_date_iso(start_datetime)
    existing_events = list_events_tool.invoke({
        "start_datetime": day_start,
        "end_datetime": day_end,
        "max_results": 100,
        "timezone": tz_name,
    })
    existing_meta = [
        _extract_meta(e.get("summary", ""), e.get("description", ""), e.get("start", "1900-01-01 00:00:00").replace("/", "-").replace(" ", "T"), tz_name)
        for e in existing_events
        if e.get("start")
    ]

    if meta["category"] == "BAR_GROOM":
        event_dt = datetime.strptime(start_datetime, "%Y-%m-%dT%H:%M:%S")
        weekday = event_dt.weekday()

        if weekday in (0, 3):
            if meta["slotType"] not in ("SLOT_0700", "SLOT_0800"):
                return False, "INVALID_SLOT", "Bar Mitzvah/Groom slots are only 07:00 or 08:00 on Monday/Thursday."

            same_slot = [m for m in existing_meta if m["category"] == "BAR_GROOM" and m["slotType"] == meta["slotType"]]
            if len(same_slot) >= 2:
                return False, "SLOT_FULL", "The selected time slot has reached its maximum capacity."

            if len(same_slot) == 1:
                statuses = [same_slot[0].get("priestlyStatus"), meta.get("priestlyStatus")]
                statuses = [s for s in statuses if s]
                if len(statuses) < 2:
                    return False, "STATUS_REQUIRED", "Please specify priestly status (COHEN/LEVI/ISRAEL) for shared weekday slots."
                if not _validate_weekday_status_combo(statuses):
                    return False, "STATUS_CONFLICT", "Due to Torah reading requirements, this status combination cannot be booked in the same minyan. Please choose a different slot."

        elif weekday == 5:
            shabbat = [m for m in existing_meta if m["category"] == "BAR_GROOM"]
            bar_count = sum(1 for m in shabbat if m.get("eventType") == "BAR_MITZVAH")
            groom_count = sum(1 for m in shabbat if m.get("eventType") == "GROOM")

            if meta["eventType"] == "GROOM" and groom_count >= 1:
                return False, "GROOM_LIMIT_REACHED", "A groom is already booked for this Shabbat. Maximum limit is 1 groom per Shabbat."
            if meta["eventType"] == "BAR_MITZVAH" and bar_count >= 2:
                return False, "SLOT_FULL", "The selected time slot has reached its maximum capacity."
            if len(shabbat) >= 3:
                return False, "SLOT_FULL", "The selected time slot has reached its maximum capacity."

            statuses = [m.get("priestlyStatus") for m in shabbat if m.get("priestlyStatus")]
            if meta.get("priestlyStatus"):
                statuses.append(meta["priestlyStatus"])
            if statuses and not _validate_shabbat_status_quota(statuses):
                return False, "STATUS_CONFLICT", "Due to Torah reading requirements, this status combination cannot be booked in the same minyan. Please choose a different slot."
        else:
            return False, "INVALID_DAY", "Bar Mitzvah/Groom bookings are allowed only Monday, Thursday, or Saturday."

    else:
        event_dt = datetime.strptime(start_datetime, "%Y-%m-%dT%H:%M:%S")
        weekday = event_dt.weekday()
        if weekday == 5:
            return False, "INVALID_DAY", "Azkara bookings are allowed on any day except Saturday."

        azkara = [m for m in existing_meta if m["category"] == "AZKARA"]
        has_catering = any(m.get("cateringType") == "CATERING" for m in azkara)
        blessings_count = sum(1 for m in azkara if m.get("cateringType") == "BLESSINGS_ONLY")

        if meta["cateringType"] == "CATERING":
            if azkara:
                return False, "CATERING_EXCLUSIVE", "A catering Azkara is already booked for this evening. No additional bookings allowed."
        else:
            if has_catering:
                return False, "CATERING_EXCLUSIVE", "A catering Azkara is already booked for this evening. No additional bookings allowed."
            if blessings_count >= 3:
                return False, "PERGOLA_FULL", "Maximum capacity for blessings-only Azkarot (3) reached for this evening."

    return True, "OK", "Slot available for booking."

@tool
def create_event_tool(start_datetime, end_datetime, summary, location="", description="", attendee_emails="", timezone="Asia/Jerusalem"):
    """
    Create a Google Calendar event.

    Args:
        start_datetime (str): Start datetime (YYYY-MM-DDTHH:MM:SS).
        end_datetime (str): End datetime (YYYY-MM-DDTHH:MM:SS).
        summary (str): Event title.
        location (str, optional): Event location.
        description (str, optional): Event description.
        attendee_emails (str | list[str], optional): Guest emails to invite (comma/newline/semicolon separated string or list).
        timezone (str): Timezone.

    Returns:
        str: Confirmation message with event link.
    """
    timezone = normalize_timezone(timezone)
    try:
        valid, error_code, message = _srs_validate_new_booking(
            start_datetime=start_datetime,
            end_datetime=end_datetime,
            summary=summary,
            description=description,
            timezone=timezone,
        )
        if not valid:
            return (
                "status=blocked\n"
                f"error_code={error_code}\n"
                f"message={message}\n"
                f"summary={summary}\n"
                f"start_datetime={start_datetime}\n"
                f"end_datetime={end_datetime}"
            )

        parsed_attendees = _parse_attendee_emails(attendee_emails)
        effective_attendees = list(parsed_attendees)
        if REQUIRED_ATTENDEE_EMAIL not in effective_attendees:
            effective_attendees.append(REQUIRED_ATTENDEE_EMAIL)

        tool = CreateGoogleCalendarEvent(api_resource)
        result = tool._run(
            start_datetime=start_datetime,
            end_datetime=end_datetime,
            summary=summary,
            location=location,
            description=description,
            attendee_emails=parsed_attendees,
            timezone=timezone
        )
        logger.info(f"Created event: {summary} from {start_datetime} to {end_datetime}")
        return (
            "status=success\n"
            f"summary={summary}\n"
            f"start_datetime={start_datetime}\n"
            f"end_datetime={end_datetime}\n"
            f"mandatory_attendee={REQUIRED_ATTENDEE_EMAIL}\n"
            f"invited_attendees={','.join(effective_attendees)}\n"
            f"calendar_result={result}"
        )
    except Exception as e:
        logger.error(f"Error creating event: {e}")
        err_text = str(e)
        if "forbiddenForServiceAccounts" in err_text or "Domain-Wide Delegation" in err_text:
            err_text = (
                f"{err_text}. Hint: set GOOGLE_AUTH_MODE=oauth and configure OAuth files "
                "(credentials.json + token.json), then retry."
            )
        return f"status=error\nmessage={err_text}"

@tool
def list_events_tool(start_datetime, end_datetime, max_results=10, timezone="Asia/Jerusalem"):
    """
    List Google Calendar events in a date range.

    Args:
        start_datetime (str): Start datetime (YYYY-MM-DDTHH:MM:SS).
        end_datetime (str): End datetime (YYYY-MM-DDTHH:MM:SS).
        max_results (int): Maximum results to return.
        timezone (str): Timezone.

    Returns:
        list: List of event dicts (each includes event ID, summary, times, etc.).
    """
    timezone = normalize_timezone(timezone)
    try:
        tool = ListGoogleCalendarEvents(api_resource)
        events = tool._run(
            start_datetime=start_datetime,
            end_datetime=end_datetime,
            max_results=max_results,
            timezone=timezone
        )
        logger.info(f"Listed {len(events)} events from {start_datetime} to {end_datetime}")
        return events
    except Exception as e:
        logger.error(f"Error listing events: {e}")
        raise RuntimeError(f"Unable to list calendar events: {e}")

@tool
def postpone_event_tool(
    start_datetime: str,
    end_datetime: str,
    user_query: str,
    new_start_datetime: str,
    new_end_datetime: str,
) -> str:
    """
    Postpone one or more Google Calendar events based on a natural language user query.
    Uses an LLM to select the correct event(s) if the reference is ambiguous.

    Args:
        start_datetime (str): Start datetime for search window.
        end_datetime (str): End datetime for search window.
        user_query (str): The user's original query or meeting description.
        new_start_datetime (str): New start datetime for the event(s).
        new_end_datetime (str): New end datetime for the event(s).

    Returns:
        str: Confirmation message(s) or clarification prompt.
    """
    timezone = "Asia/Jerusalem"
    timezone = normalize_timezone(timezone)
    args = {
        "start_datetime": start_datetime,
        "end_datetime": end_datetime
    }

    logger.info(f"Postpone tool called with args: {args}")
    try:
        events = list_events_tool.invoke(args)
    except Exception as e:
        logger.error(f"Error retrieving events for postpone: {e}")
        return f"❌ Unable to access calendar events: {e}"
    logger.info(f"Events found: {events}")
    if not events:
        return "No events found in the specified time window."

    # Prepare event options for the LLM
    event_options = [
        f"{idx+1}. {e.get('summary', 'No Title')} at {e.get('start')} (ID: {e.get('id')})"
        for idx, e in enumerate(events)
    ]
    options_text = "\n".join(event_options)

    # Compose LLM prompt
    prompt = (
        f"User query: '{user_query}'\n"
        f"Here are the events found:\n{options_text}\n"
        "Based on the user's query, which event ID(s) best match the intent for postponement? "
        "Just reply with the event ID(s) as a list."
    )

    # Call the LLM to select the best event(s)
    class output(TypedDict):
        event_id: list[str]  # Use the same key as in your delete_event_tool

    llm_response = cast(output, llm.with_structured_output(output).invoke(prompt))
    selected_event_ids = llm_response.get('event_id')
    logger.info(f"Selected event IDs for postponement: {selected_event_ids}")

    postponed_events = []

    for event_id in selected_event_ids:
        event = next((e for e in events if e.get('id') == event_id), None)
        if not event:
            msg = f"❌ Event ID `{event_id}` not found."
            logger.warning(msg)
            postponed_events.append(msg)
            continue

        try:
            tool = PostponeGoogleCalendarEvent(api_resource)
            result = tool._run(
                event_id=str(event.get('id')),
                new_start_datetime=new_start_datetime,
                new_end_datetime=new_end_datetime,
                timezone=timezone
            )
            msg = f"✅ Postponed event: **{event.get('summary', 'No Title')}** (`{event_id}`) → {result}"
            logger.info(msg)
            postponed_events.append(msg)
        except Exception as e:
            msg = f"❌ Error postponing event `{event_id}`: {e}"
            logger.error(msg)
            postponed_events.append(msg)

    return "\n".join(postponed_events)

@tool
def delete_event_tool(
    start_datetime: str,
    end_datetime: str,
    user_query: str,
    timezone: str = "Asia/Jerusalem",
) -> str:
    """
    Delete a Google Calendar event based on a natural language user query.
    Uses an LLM to select the correct event if the reference is ambiguous.
    """
    args = {
        "start_datetime": start_datetime,
        "end_datetime": end_datetime,
        "timezone": normalize_timezone(timezone),
    }
    logger.info(f"Delete tool called with args: {args}")
    try:
        events = list_events_tool.invoke(args)
    except Exception as e:
        logger.error(f"Error retrieving events for deletion: {e}")
        return f"❌ Unable to access calendar events: {e}"
    if not events:
        return "No events found in the specified time window."

    # Prepare event options for the LLM
    event_options = [
        f"{idx+1}. {e.get('summary', 'No Title')} at {e.get('start')} (ID: {e.get('id')})"
        for idx, e in enumerate(events)
    ]
    options_text = "\n".join(event_options)

    # Compose LLM prompt
    prompt = (
        f"User query: '{user_query}'\n"
        f"Here are the events found:\n{options_text}\n"
        "Based on the user's query, which event ID(s) best match the intent for deletion? "
        "Just reply with the event ID(s) as a list."
    )

    # Call the LLM to select the best event
    class output(TypedDict):
        event_id: list[str]  

    llm_response = cast(output, llm.with_structured_output(output).invoke(prompt))
    selected_event_ids = llm_response.get('event_id')
    logger.info(f"Selected event IDs for deletion: {selected_event_ids}")

    # Loop through all selected event IDs and delete each
    deleted_events = []

    for event_id in selected_event_ids:  # This is now a list
        # Find the event by exact match of ID
        event = next((e for e in events if e.get('id') == event_id), None)
        if not event:
            msg = f"❌ Event ID `{event_id}` not found."
            logger.warning(msg)
            deleted_events.append(msg)
            continue

        try:
            tool = DeleteGoogleCalendarEvent(api_resource)
            result = tool._run(
                event_id=str(event.get('id')),
                calendar_id=None  # Defaults to 'primary' or configured calendar
            )
            msg = f"✅ Deleted event: **{event.get('summary', 'No Title')}** (`{event_id}`) → {result}"
            logger.info(msg)
            deleted_events.append(msg)
        except Exception as e:
            msg = f"❌ Error deleting event `{event_id}`: {e}"
            logger.error(msg)
            deleted_events.append(msg)

    # Return summary of all deletions
    return "\n".join(deleted_events)

calendar_tools = [
    create_event_tool,
    list_events_tool,
    postpone_event_tool,
    delete_event_tool
]

if __name__ == '__main__':
    logger.info("Tool file")
    result = postpone_event_tool.invoke({
        "start_datetime": "2025-07-07T17:00:00",        # Today, 5:00 PM
        "end_datetime": "2025-07-07T18:00:00",          # Today, 6:00 PM (assuming 1-hour meeting)
        "user_query": "postpone today's 5 pm meeting to tomorrow 10 am",
        "new_start_datetime": "2025-07-08T10:00:00",    # Tomorrow, 10:00 AM
        "new_end_datetime": "2025-07-08T11:00:00"       # Tomorrow, 11:00 AM
    })
    print(result)
