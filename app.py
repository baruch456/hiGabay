from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, Literal
from datetime import date, datetime, time
from uuid import uuid4
import os
import pytz
from googleapiclient.discovery import build

# Import your graph and related components
from src.graph import graph  # Adjust import path as needed
from src.tool import create_event_tool
from src.utilities import CALENDAR_ID, api_resource, credentials

app = FastAPI()


BOOKING_SPREADSHEET_ID = os.getenv(
    "BOOKING_SPREADSHEET_ID",
    "1d96PDhJ5rmecdycVdChVp5HSh2Nqs183lkBr6GKCSuI",
)
BOOKING_SHEET_NAME = os.getenv("BOOKING_SHEET_NAME", "Bookings")
BOOKING_HEADERS = [
    "booking_id",
    "event_date",
    "category",
    "event_type",
    "slot_type",
    "priestly_status",
    "catering_type",
    "contact_name",
    "contact_phone",
    "contact_email",
    "amount_due",
    "paid_status",
    "notes",
    "created_at",
]
sheets_service = build("sheets", "v4", credentials=credentials)


def _ensure_booking_headers() -> None:
    sheets_service.spreadsheets().values().update(
        spreadsheetId=BOOKING_SPREADSHEET_ID,
        range=f"{BOOKING_SHEET_NAME}!A1:N1",
        valueInputOption="RAW",
        body={"values": [BOOKING_HEADERS]},
    ).execute()


def _next_booking_row_number() -> int:
    """Return next writable row based on non-empty cells in column A."""
    resp = sheets_service.spreadsheets().values().get(
        spreadsheetId=BOOKING_SPREADSHEET_ID,
        range=f"{BOOKING_SHEET_NAME}!A:A",
    ).execute()
    values = resp.get("values", [])
    return len(values) + 1


def _append_booking_row(booking_id: str, booking: "BookingPayload") -> None:
    _ensure_booking_headers()
    amount_due = max(0.0, float(booking.amountDue or 0.0))
    paid_status = "UNPAID"
    row = [
        booking_id,
        booking.date.isoformat(),
        booking.category,
        booking.eventType or "",
        booking.slotType,
        booking.priestlyStatus or "",
        booking.cateringType or "",
        booking.contactName,
        booking.contactPhone,
        booking.contactEmail,
        amount_due,
        paid_status,
        booking.notes or "",
        datetime.now().isoformat(),
    ]
    row_number = _next_booking_row_number()
    sheets_service.spreadsheets().values().update(
        spreadsheetId=BOOKING_SPREADSHEET_ID,
        range=f"{BOOKING_SHEET_NAME}!A{row_number}:N{row_number}",
        valueInputOption="RAW",
        body={"values": [row]},
    ).execute()



class UserInput(BaseModel):
    user_input: str


class BookingPayload(BaseModel):
    date: date
    contactName: str
    contactPhone: str
    contactEmail: str
    notes: Optional[str] = ""
    category: Literal["BAR_GROOM", "AZKARA"]
    eventType: Optional[Literal["BAR_MITZVAH", "GROOM"]] = None
    slotType: Literal["SLOT_0700", "SLOT_0800", "POST_ARVIT"]
    priestlyStatus: Optional[Literal["COHEN", "LEVI", "ISRAEL"]] = None
    cateringType: Optional[Literal["BLESSINGS_ONLY", "CATERING"]] = None
    amountDue: Optional[float] = 0


class BookingRequest(BaseModel):
    intent: Literal["BOOKING_REQUEST"]
    version: Literal["srs_v2.0"]
    requestTimestamp: date
    booking: BookingPayload


class MarkPaidRequest(BaseModel):
    bookingId: str
    isPaid: bool


def _validate_booking_payload(payload: BookingRequest) -> None:
    b = payload.booking
    weekday = b.date.weekday()  # Monday=0 ... Sunday=6

    if b.category == "BAR_GROOM":
        if b.eventType is None:
            raise ValueError("eventType is required for category BAR_GROOM")
        if b.priestlyStatus is None:
            raise ValueError("priestlyStatus is required for category BAR_GROOM")
        if b.cateringType is not None:
            raise ValueError("cateringType must be null for category BAR_GROOM")
        if b.slotType not in ("SLOT_0700", "SLOT_0800"):
            raise ValueError("slotType must be SLOT_0700 or SLOT_0800 for BAR_GROOM")
        if weekday not in (0, 3, 5):
            raise ValueError("BAR_GROOM date must be Monday, Thursday, or Saturday")

    if b.category == "AZKARA":
        if b.cateringType is None:
            raise ValueError("cateringType is required for category AZKARA")
        if b.eventType is not None:
            raise ValueError("eventType must be null for category AZKARA")
        if b.priestlyStatus is not None:
            raise ValueError("priestlyStatus must be null for category AZKARA")
        if b.slotType != "POST_ARVIT":
            raise ValueError("slotType must be POST_ARVIT for category AZKARA")
        if weekday == 5:
            raise ValueError("AZKARA date cannot be Saturday")


def _build_booking_message(payload: BookingRequest) -> str:
    import json
    return (
        "Process this booking request JSON exactly by policy. "
        "If fields are missing or rule conflicts exist, ask short follow-up questions.\n\n"
        + json.dumps(payload.model_dump(mode="json"), indent=2)
    )


def _event_datetimes_from_slot(booking: BookingPayload) -> tuple[str, str]:
    booking_date = booking.date.isoformat()

    # Canonical slot-to-time mapping for deterministic booking flow.
    if booking.slotType == "SLOT_0700":
        return f"{booking_date}T07:00:00", f"{booking_date}T08:00:00"
    if booking.slotType == "SLOT_0800":
        return f"{booking_date}T08:00:00", f"{booking_date}T09:00:00"
    # POST_ARVIT
    return f"{booking_date}T20:00:00", f"{booking_date}T21:00:00"


def _event_text_from_booking(booking: BookingPayload) -> tuple[str, str]:
    if booking.category == "BAR_GROOM":
        summary = f"{booking.eventType} Reservation - {booking.contactName}"
        description = (
            f"Event Type: {booking.eventType}\n"
            f"Priestly Status: {booking.priestlyStatus}\n"
            f"Contact Name: {booking.contactName}\n"
            f"Contact Phone: {booking.contactPhone}\n"
            f"Contact Email: {booking.contactEmail}\n"
            f"Notes: {booking.notes or ''}"
        )
        return summary, description

    summary = f"AZKARA {booking.cateringType} - {booking.contactName}"
    description = (
        f"Category: AZKARA\n"
        f"Catering Type: {booking.cateringType}\n"
        f"Contact Name: {booking.contactName}\n"
        f"Contact Phone: {booking.contactPhone}\n"
        f"Contact Email: {booking.contactEmail}\n"
        f"Notes: {booking.notes or ''}"
    )
    return summary, description


def _parse_tool_status(tool_result: str) -> tuple[str, str]:
    text = str(tool_result)
    if "status=success" in text:
        return "success", text
    if "status=blocked" in text:
        return "blocked", text
    return "error", text


def _extract_assistant_text(result):
    """Extract the latest assistant text from graph output for simple API clients like n8n."""
    if isinstance(result, dict) and "messages" in result:
        for msg in reversed(result["messages"]):
            if isinstance(msg, dict):
                if msg.get("type") == "ai" and msg.get("content"):
                    return str(msg.get("content"))
                if msg.get("role") == "assistant" and msg.get("content"):
                    return str(msg.get("content"))

            content = getattr(msg, "content", None)
            msg_type = getattr(msg, "type", None)
            if msg_type == "ai" and content:
                return str(content)

    return str(result)


def _coerce_row(row: list[str], width: int = 14) -> list[str]:
    values = list(row)
    while len(values) < width:
        values.append("")
    return values[:width]


def _read_booking_rows() -> list[list[str]]:
    resp = sheets_service.spreadsheets().values().get(
        spreadsheetId=BOOKING_SPREADSHEET_ID,
        range=f"{BOOKING_SHEET_NAME}!A2:N",
    ).execute()
    return [_coerce_row(r) for r in resp.get("values", [])]


def _row_to_booking_dict(row: list[str]) -> dict:
    return {
        "bookingId": row[0],
        "eventDate": row[1],
        "category": row[2],
        "eventType": row[3],
        "slotType": row[4],
        "priestlyStatus": row[5],
        "cateringType": row[6],
        "contactName": row[7],
        "contactPhone": row[8],
        "contactEmail": row[9],
        "amountDue": row[10],
        "paidStatus": row[11],
        "notes": row[12],
        "createdAt": row[13],
    }

@app.post("/invoke")
async def invoke_graph(data: UserInput):
    try:
        # Wrap the user input as a single "user" message
        messages = [{"role": "user", "content": data.user_input}]
        result = graph.invoke({"messages": messages})
        return {"status": "success", "result": result}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/calendar/events")
async def list_calendar_events(start_date: date, end_date: date):
    if end_date <= start_date:
        raise HTTPException(status_code=400, detail="end_date must be after start_date")

    calendar_timezone = pytz.timezone("Asia/Jerusalem")
    time_min = calendar_timezone.localize(datetime.combine(start_date, time.min))
    time_max = calendar_timezone.localize(datetime.combine(end_date, time.min))
    events = []
    page_token = None
    while True:
        result = (
            api_resource.events()
            .list(
                calendarId=CALENDAR_ID,
                timeMin=time_min.isoformat(),
                timeMax=time_max.isoformat(),
                maxResults=2500,
                singleEvents=True,
                orderBy="startTime",
                pageToken=page_token,
            )
            .execute()
        )
        events.extend(result.get("items", []))
        page_token = result.get("nextPageToken")
        if not page_token:
            break

    return {
        "status": "success",
        "events": [
            {
                "summary": event.get("summary", "Busy"),
                "start": event.get("start", {}),
                "end": event.get("end", {}),
            }
            for event in events
        ],
    }


@app.post("/bookings/submit")
async def submit_booking(data: BookingRequest):
    """
    Strict booking endpoint that accepts a canonical SRS JSON payload.
    """
    try:
        _validate_booking_payload(data)

        booking = data.booking
        booking_id = str(uuid4())
        amount_due = max(0.0, float(booking.amountDue or 0.0))
        paid_status = "UNPAID"
        start_datetime, end_datetime = _event_datetimes_from_slot(booking)
        summary, description = _event_text_from_booking(booking)
        description = (
            f"Booking ID: {booking_id}\n"
            f"Amount Due: {amount_due}\n"
            f"Paid Status: {paid_status}\n"
            + description
        )

        tool_args = {
            "start_datetime": start_datetime,
            "end_datetime": end_datetime,
            "summary": summary,
            "location": "Synagogue",
            "description": description,
            "attendee_emails": booking.contactEmail,
            "timezone": "Asia/Jerusalem",
        }

        tool_result = create_event_tool.invoke(tool_args)
        parsed_status, parsed_message = _parse_tool_status(tool_result)

        if parsed_status == "success":
            sheet_status = "not_written"
            try:
                _append_booking_row(booking_id, booking)
                sheet_status = "written"
            except Exception as sheet_error:
                sheet_status = f"write_failed: {sheet_error}"
            answer = "Booking created successfully."
        elif parsed_status == "blocked":
            sheet_status = "not_written"
            answer = "Booking blocked by policy constraints."
        else:
            sheet_status = "not_written"
            answer = "Booking failed due to an internal error."

        return {
            "status": parsed_status,
            "answer": answer,
            "message": parsed_message,
            "bookingId": booking_id,
            "sheetStatus": sheet_status,
            "payload": data.model_dump(mode="json"),
            "toolArgs": tool_args,
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/bookings/list")
async def list_bookings(paid_status: Optional[str] = Query(default=None)):
    try:
        rows = _read_booking_rows()
        items = [_row_to_booking_dict(r) for r in rows if r[0]]

        if paid_status:
            wanted = paid_status.strip().upper()
            items = [item for item in items if item.get("paidStatus", "").upper() == wanted]

        return {"status": "success", "count": len(items), "bookings": items}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/bookings/mark-paid")
async def mark_booking_paid(data: MarkPaidRequest):
    try:
        rows = _read_booking_rows()
        target_row_number = None
        for idx, row in enumerate(rows):
            if row[0] == data.bookingId:
                target_row_number = idx + 2
                break

        if target_row_number is None:
            return {"status": "error", "message": f"booking_id not found: {data.bookingId}"}

        new_status = "PAID" if data.isPaid else "UNPAID"
        sheets_service.spreadsheets().values().update(
            spreadsheetId=BOOKING_SPREADSHEET_ID,
            range=f"{BOOKING_SHEET_NAME}!L{target_row_number}",
            valueInputOption="RAW",
            body={"values": [[new_status]]},
        ).execute()

        return {
            "status": "success",
            "bookingId": data.bookingId,
            "paidStatus": new_status,
            "row": target_row_number,
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/invoke-simple")
async def invoke_graph_simple(data: UserInput):
    """
    Beginner-friendly endpoint for automation tools like n8n.
    Always returns a flat response with an "answer" string.
    """
    try:
        messages = [{"role": "user", "content": data.user_input}]
        result = graph.invoke({"messages": messages})
        answer = _extract_assistant_text(result)
        return {"success": True, "answer": answer, "raw": result}
    except Exception as e:
        return {"success": False, "answer": "", "error": str(e)}

@app.get("/health")
async def health_check():
    return {"status": "API is running"}
