from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from langchain_google_community import CalendarToolkit
from googleapiclient.discovery import build
from dateutil import parser, tz
from datetime import datetime
from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional, Type
import os
from dotenv import load_dotenv


# Load .env before reading configuration values.
load_dotenv()


# Shared OAuth scopes for calendar + payment sheet workflows.
SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# Auth mode: auto | oauth | service_account
AUTH_MODE = os.getenv("GOOGLE_AUTH_MODE", "auto").strip().lower()

# OAuth files.
OAUTH_CLIENT_SECRETS_FILE = os.getenv("GOOGLE_OAUTH_CLIENT_SECRETS", "credentials.json")
OAUTH_TOKEN_FILE = os.getenv("GOOGLE_OAUTH_TOKEN_FILE", "token.json")

# Path of service account JSON key.
# Render mounts secrets in /etc/secrets, while local runs often keep it in project root.
SERVICE_ACCOUNT_FILE = os.getenv("SERVICE_ACCOUNT_FILE", "/etc/secrets/service-account.json")
if not os.path.exists(SERVICE_ACCOUNT_FILE) and os.path.exists("service-account.json"):
    SERVICE_ACCOUNT_FILE = "service-account.json"

# Calendar ID must come from environment.
CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID")
if not CALENDAR_ID:
    raise ValueError("GOOGLE_CALENDAR_ID is not set. Please add it to your .env file.")

# Mandatory attendee that is always added to created events.
MANDATORY_ATTENDEE_EMAIL = os.getenv("MANDATORY_ATTENDEE_EMAIL").strip().lower()

print(f"[calendar-config] GOOGLE_AUTH_MODE={AUTH_MODE}")
print(f"[calendar-config] SERVICE_ACCOUNT_FILE={SERVICE_ACCOUNT_FILE}")
print(f"[calendar-config] OAUTH_CLIENT_SECRETS_FILE={OAUTH_CLIENT_SECRETS_FILE}")
print(f"[calendar-config] OAUTH_TOKEN_FILE={OAUTH_TOKEN_FILE}")
print(f"[calendar-config] GOOGLE_CALENDAR_ID={CALENDAR_ID}")
print(f"[calendar-config] MANDATORY_ATTENDEE_EMAIL={MANDATORY_ATTENDEE_EMAIL}")


def _build_service_account_credentials():
    if not os.path.exists(SERVICE_ACCOUNT_FILE):
        raise FileNotFoundError(
            f"Service account file not found: {SERVICE_ACCOUNT_FILE}. "
            "Set SERVICE_ACCOUNT_FILE or place service-account.json in project root."
        )
    return service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE,
        scopes=SCOPES,
    )


def _build_oauth_credentials():
    creds = None

    if os.path.exists(OAUTH_TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(OAUTH_TOKEN_FILE, SCOPES)

    if creds and not creds.has_scopes(SCOPES):
        # Existing token may have narrower scopes (e.g. sheets-only).
        creds = None

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            with open(OAUTH_TOKEN_FILE, "w", encoding="utf-8") as token_file:
                token_file.write(creds.to_json())
        except RefreshError:
            creds = None

    if not creds or not creds.valid:
        if not os.path.exists(OAUTH_CLIENT_SECRETS_FILE):
            raise FileNotFoundError(
                f"OAuth client secrets file not found: {OAUTH_CLIENT_SECRETS_FILE}. "
                "Create an OAuth Desktop App credential in Google Cloud and set GOOGLE_OAUTH_CLIENT_SECRETS."
            )
        flow = InstalledAppFlow.from_client_secrets_file(OAUTH_CLIENT_SECRETS_FILE, SCOPES)
        creds = flow.run_local_server(port=0)
        with open(OAUTH_TOKEN_FILE, "w", encoding="utf-8") as token_file:
            token_file.write(creds.to_json())

    return creds


def _resolve_google_credentials():
    oauth_exists = os.path.exists(OAUTH_TOKEN_FILE) or os.path.exists(OAUTH_CLIENT_SECRETS_FILE)

    if AUTH_MODE == "oauth":
        print("[calendar-config] Using OAuth credentials (forced by GOOGLE_AUTH_MODE=oauth).")
        return _build_oauth_credentials()

    if AUTH_MODE == "service_account":
        print("[calendar-config] Using service account credentials (forced by GOOGLE_AUTH_MODE=service_account).")
        return _build_service_account_credentials()

    if oauth_exists:
        print("[calendar-config] OAuth files detected, using OAuth credentials (auto mode).")
        return _build_oauth_credentials()

    print("[calendar-config] OAuth files not detected, using service account credentials (auto mode).")
    return _build_service_account_credentials()


credentials = _resolve_google_credentials()

# build teh api resource manually
api_resource = build('calendar', 'v3', credentials=credentials)

# initialize the toolkit with the api_resource
toolkit = CalendarToolkit(api_resource=api_resource)


class GoogleCalendarBaseTool:
    """Base class for Google Calendar tools using a service account."""
    api_resource = None

    def __init__(self, api_resource):
        self.api_resource = api_resource

    @classmethod
    def from_api_resource(cls, api_resource):
        return cls(api_resource=api_resource)


class GetEventsSchema(BaseModel):
    start_datetime: str = Field(..., description="Start datetime (YYYY-MM-DDTHH:MM:SS)")
    end_datetime: str = Field(..., description="End datetime (YYYY-MM-DDTHH:MM:SS)")
    max_results: int = Field(default=10, description="Max results to return")
    timezone: str = Field(default="Asia/Kolkata", description="Timezone (TZ database name)")

class ListGoogleCalendarEvents(GoogleCalendarBaseTool):
    def _parse_event(self, event, timezone):
        start = event['start'].get('dateTime', event['start'].get('date'))
        start = parser.parse(start).astimezone(tz.gettz(timezone)).strftime('%Y/%m/%d %H:%M:%S')
        end = event['end'].get('dateTime', event['end'].get('date'))
        end = parser.parse(end).astimezone(tz.gettz(timezone)).strftime('%Y/%m/%d %H:%M:%S')
        event_parsed = dict(start=start, end=end)
        for field in ['summary', 'description', 'location', 'hangoutLink']:
            event_parsed[field] = event.get(field, None)
        # ADD THIS LINE:
        event_parsed['id'] = event.get('id')
        return event_parsed


    def _run(self, start_datetime, end_datetime, max_results=10, timezone="Asia/Kolkata"):
        calendar_id = CALENDAR_ID
        events = []
        start = datetime.strptime(start_datetime, '%Y-%m-%dT%H:%M:%S')
        start = start.replace(tzinfo=tz.gettz(timezone)).isoformat()
        end = datetime.strptime(end_datetime, '%Y-%m-%dT%H:%M:%S')
        end = end.replace(tzinfo=tz.gettz(timezone)).isoformat()
        events_result = self.api_resource.events().list(
            calendarId=calendar_id,
            timeMin=start,
            timeMax=end,
            maxResults=max_results,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        cal_events = events_result.get('items', [])
        events.extend(cal_events)
        events = sorted(events, key=lambda x: x['start'].get('dateTime', x['start'].get('date')))
        return [self._parse_event(e, timezone) for e in events]
    


class CreateEventSchema(BaseModel):
    start_datetime: str = Field(..., description="Start datetime (YYYY-MM-DDTHH:MM:SS)")
    end_datetime: str = Field(..., description="End datetime (YYYY-MM-DDTHH:MM:SS)")
    summary: str = Field(..., description="Event title")
    location: Optional[str] = Field(default="", description="Event location")
    description: Optional[str] = Field(default="", description="Event description")
    attendee_emails: Optional[List[str]] = Field(default=None, description="Guest emails that should receive RSVP invitations")
    timezone: str = Field(default="Asia/Kolkata", description="Timezone (TZ database name)")

class CreateGoogleCalendarEvent(GoogleCalendarBaseTool):
    def _run(self, start_datetime, end_datetime, summary, location="", description="", attendee_emails=None, timezone="Asia/Kolkata"):
        calendar_id = CALENDAR_ID
        start = datetime.strptime(start_datetime, '%Y-%m-%dT%H:%M:%S')
        start = start.replace(tzinfo=tz.gettz(timezone)).isoformat()
        end = datetime.strptime(end_datetime, '%Y-%m-%dT%H:%M:%S')
        end = end.replace(tzinfo=tz.gettz(timezone)).isoformat()

        normalized_attendees: List[str] = []
        for email in attendee_emails or []:
            if isinstance(email, str):
                cleaned = email.strip().lower()
                if cleaned and cleaned not in normalized_attendees:
                    normalized_attendees.append(cleaned)
        if MANDATORY_ATTENDEE_EMAIL and MANDATORY_ATTENDEE_EMAIL not in normalized_attendees:
            normalized_attendees.append(MANDATORY_ATTENDEE_EMAIL)

        body = {
            'summary': summary,
            'start': {'dateTime': start},
            'end': {'dateTime': end},
            'attendees': [{'email': email} for email in normalized_attendees],
            'guestsCanModify': False,
            'guestsCanInviteOthers': False,
            'guestsCanSeeOtherGuests': True,
        }
        if location:
            body['location'] = location
        if description:
            body['description'] = description
        event = self.api_resource.events().insert(
            calendarId=calendar_id,
            body=body,
            sendUpdates='all'
        ).execute()
        return "Event created: " + event.get('htmlLink', 'Failed to create event')


class DeleteEventSchema(BaseModel):
    event_id: str = Field(..., description="The event ID to delete.")
    calendar_id: Optional[str] = Field(
        default=None, description="The calendar ID. Defaults to your test calendar."
    )

class DeleteGoogleCalendarEvent:
    """
    Delete an event from your Google Calendar.

    - Requires only the event ID.
    - Always uses your test calendar ID unless another is specified.
    """

    def __init__(self, api_resource):
        self.api_resource = api_resource
        self.calendar_id = CALENDAR_ID

    def _run(self, event_id, calendar_id=None):
        calendar_id = calendar_id or self.calendar_id
        try:
            self.api_resource.events().delete(
                calendarId=calendar_id,
                eventId=event_id
            ).execute()
            return f"Event {event_id} deleted from calendar {calendar_id}."
        except Exception as e:
            return f"Failed to delete event: {e}"
        


class PostponeEventSchema(BaseModel):
    event_id: str = Field(..., description="The event ID to postpone.")
    new_start_datetime: str = Field(..., description="New start datetime (YYYY-MM-DDTHH:MM:SS)")
    new_end_datetime: str = Field(..., description="New end datetime (YYYY-MM-DDTHH:MM:SS)")
    timezone: str = Field(default="Asia/Kolkata", description="Timezone (TZ database name)")
    calendar_id: Optional[str] = Field(
        default=None, description="The calendar ID. Defaults to your test calendar."
    )

class PostponeGoogleCalendarEvent:
    """
    Postpone (reschedule) an event in your Google Calendar.

    - Requires event ID and new start/end datetimes.
    - Always uses your test calendar ID unless another is specified.
    """

    def __init__(self, api_resource):
        self.api_resource = api_resource
        self.calendar_id = CALENDAR_ID

    def _run(self, event_id, new_start_datetime, new_end_datetime, timezone="Asia/Kolkata", calendar_id=None):
        calendar_id = calendar_id or self.calendar_id
        try:
            # Get the existing event
            event = self.api_resource.events().get(
                calendarId=calendar_id,
                eventId=event_id
            ).execute()

            # Format new times
            start = datetime.strptime(new_start_datetime, '%Y-%m-%dT%H:%M:%S').replace(tzinfo=tz.gettz(timezone)).isoformat()
            end = datetime.strptime(new_end_datetime, '%Y-%m-%dT%H:%M:%S').replace(tzinfo=tz.gettz(timezone)).isoformat()

            event['start']['dateTime'] = start
            event['end']['dateTime'] = end
            event['start']['timeZone'] = timezone
            event['end']['timeZone'] = timezone

            updated_event = self.api_resource.events().update(
                calendarId=calendar_id,
                eventId=event_id,
                body=event
            ).execute()

            return f"Event postponed: {updated_event.get('htmlLink', 'No link')}"
        except Exception as e:
            return f"Failed to postpone event: {e}"

if __name__ == '__main__':
    print("All okay")
