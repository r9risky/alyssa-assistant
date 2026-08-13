"""
Calendar/email integration - "what's my next meeting", "any important
emails", and a proactive "you have a meeting in 10 minutes" via
check_watch(). Uses your own Google account (Calendar + Gmail read-only),
via OAuth - Alyssa never sees your password, and only ever reads mail/
calendar, never sends or deletes anything.

ONE-TIME SETUP

  1. Make sure the Google auth libraries are installed:
     pip install google-auth-oauthlib google-api-python-client

  2. First time you ask Alyssa about your calendar/email, a browser tab
     opens for you to sign in and approve read-only access. If Google asks
     for a client ID/secret, it means the built-in sign-in flow hasn't been
     configured yet for this install; in that case, create a Desktop OAuth
     client in Google Cloud Console and save it as credentials.json next to
     this plugin (or next to main.py/config.py) so Alyssa can use it.

  3. After you sign in, a token.json is saved locally so you're not asked
     again until the token expires or is revoked.

Everything below simply no-ops with a clear spoken message if the plugin is
not yet authenticated, so this plugin is safe to leave installed even before
setup is complete.
"""
import datetime
import os
import sys
import webbrowser

UNTRUSTED_OUTPUTS = {
    "get_next_meeting", "get_todays_schedule", "check_important_emails",
}


def _format_time(value: datetime.datetime) -> str:
    """Portable 12-hour time without a leading zero."""
    return value.strftime("%I:%M %p").lstrip("0")

try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    _GOOGLE_LIBS_AVAILABLE = True
except Exception:
    Request = Credentials = InstalledAppFlow = build = None
    _GOOGLE_LIBS_AVAILABLE = False

# Read-only scopes on purpose - this plugin only ever reads, never sends/
# deletes/modifies anything in your calendar or inbox.
_SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
]

if getattr(sys, "frozen", False):
    _BASE_DIR = os.path.dirname(sys.executable)
else:
    _BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CREDENTIALS_PATH = os.path.join(_BASE_DIR, "credentials.json")
_TOKEN_PATH = os.path.join(_BASE_DIR, "token.json")

# How many minutes before a meeting the proactive "you have a meeting in
# X minutes" alert fires.
MEETING_WARNING_MINUTES = 10
WATCH_INTERVAL_SECONDS = 60

_LIBS_MISSING_MSG = (
    "I can't check your calendar/email - the Google API libraries aren't "
    "installed. Run: pip install google-auth-oauthlib google-api-python-client"
)
_NO_CREDENTIALS_MSG = (
    "I'm not connected to your Google account yet - I can open a sign-in prompt "
    "for you right away so you can connect your calendar and email."
)

_service_cache = {}


def _find_credentials_path():
    candidates = []
    if _CREDENTIALS_PATH:
        candidates.append(_CREDENTIALS_PATH)
    candidates.extend(
        [
            os.path.join(_BASE_DIR, "plugins", "credentials.json"),
            os.path.join(_BASE_DIR, "plugins", "calendar_gmail", "credentials.json"),
            os.path.join(_BASE_DIR, "config", "credentials.json"),
            os.path.join(_BASE_DIR, "credentials.json"),
        ]
    )
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return None


def _get_credentials():
    if not _GOOGLE_LIBS_AVAILABLE or not Credentials or not InstalledAppFlow or not Request:
        return None

    credentials_path = _find_credentials_path()

    creds = None
    if os.path.exists(_TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(_TOKEN_PATH, _SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                creds = None

        if not creds:
            if credentials_path is None:
                return None
            try:
                flow = InstalledAppFlow.from_client_secrets_file(credentials_path, _SCOPES)
                creds = flow.run_local_server(port=0)
            except Exception:
                return None

            with open(_TOKEN_PATH, "w", encoding="utf-8") as f:
                f.write(creds.to_json())

    return creds


def _get_service(name: str, version: str):
    """Cached per-API service client, since building one talks to Google's
    discovery endpoint - not something to redo on every single request."""
    key = f"{name}:{version}"
    if key in _service_cache:
        return _service_cache[key]
    if not build or not _GOOGLE_LIBS_AVAILABLE:
        return None
    creds = _get_credentials()
    if creds is None:
        return None
    service = build(name, version, credentials=creds)
    _service_cache[key] = service
    return service


def _prompt_authentication() -> str:
    auth_url = "https://console.cloud.google.com/"
    try:
        webbrowser.open(auth_url)
    except Exception:
        pass
    return (
        "I opened Google’s sign-in page for you. Please sign in and create a Desktop OAuth client, "
        "then save it as credentials.json so I can connect to your calendar and email."
    )


def _availability_check():
    if not _GOOGLE_LIBS_AVAILABLE:
        return _LIBS_MISSING_MSG
    if not os.path.exists(_TOKEN_PATH) and _find_credentials_path() is None:
        return _NO_CREDENTIALS_MSG
    return None


def _fetch_upcoming_events(max_results: int = 5):
    service = _get_service("calendar", "v3")
    now = datetime.datetime.utcnow().isoformat() + "Z"
    result = (
        service.events()
        .list(
            calendarId="primary",
            timeMin=now,
            maxResults=max_results,
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )
    return result.get("items", [])


def _event_start(event) -> datetime.datetime | None:
    start = event.get("start", {})
    raw = start.get("dateTime") or start.get("date")
    if not raw:
        return None
    try:
        return datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def get_next_meeting() -> str:
    error = _availability_check()
    if error:
        if error == _NO_CREDENTIALS_MSG:
            return _prompt_authentication()
        return error
    try:
        events = _fetch_upcoming_events(max_results=1)
    except Exception as e:
        return f"I couldn't reach Google Calendar just now - {e}"
    if not events:
        return "You don't have anything else on your calendar."
    event = events[0]
    start = _event_start(event)
    title = event.get("summary", "an untitled event")
    if start is None:
        return f"Your next event is '{title}', but I couldn't read its time."
    local_start = start.astimezone()
    when = f"{local_start.strftime('%A')} at {_format_time(local_start)}"
    return f"Your next event is '{title}' {when}."


def get_todays_schedule() -> str:
    error = _availability_check()
    if error:
        if error == _NO_CREDENTIALS_MSG:
            return _prompt_authentication()
        return error
    try:
        events = _fetch_upcoming_events(max_results=15)
    except Exception as e:
        return f"I couldn't reach Google Calendar just now - {e}"
    today = datetime.datetime.now().astimezone().date()
    todays = []
    for event in events:
        start = _event_start(event)
        if start is None:
            continue
        if start.astimezone().date() != today:
            continue
        todays.append((start.astimezone(), event.get("summary", "untitled")))
    if not todays:
        return "Nothing else on your calendar for today."
    lines = [f"{_format_time(t)} - {title}" for t, title in todays]
    return "Today: " + "; ".join(lines) + "."


def check_important_emails(max_results: int = 5) -> str:
    """Reports unread emails currently in the inbox (Gmail's own 'unread
    in inbox' view, not a custom importance model)."""
    error = _availability_check()
    if error:
        if error == _NO_CREDENTIALS_MSG:
            return _prompt_authentication()
        return error
    try:
        service = _get_service("gmail", "v1")
        result = (
            service.users()
            .messages()
            .list(userId="me", labelIds=["INBOX", "UNREAD"], maxResults=max_results)
            .execute()
        )
        messages = result.get("messages", [])
        if not messages:
            return "No unread emails in your inbox."
        summaries = []
        for m in messages:
            full = (
                service.users()
                .messages()
                .get(userId="me", id=m["id"], format="metadata", metadataHeaders=["From", "Subject"])
                .execute()
            )
            headers = {h["name"]: h["value"] for h in full.get("payload", {}).get("headers", [])}
            sender = headers.get("From", "someone").split("<")[0].strip()
            subject = headers.get("Subject", "(no subject)")
            summaries.append(f"{sender}: {subject}")
        return f"You have {result.get('resultSizeEstimate', len(messages))} unread email(s). " + "; ".join(summaries)
    except Exception as e:
        return f"I couldn't check your email just now - {e}"


# --- Proactive "meeting in N minutes" ---------------------------------------
_warned_event_ids = set()


def check_watch():
    if _availability_check():
        return None  # not set up / libs missing - stay silent, not a repeated nag
    try:
        events = _fetch_upcoming_events(max_results=5)
    except Exception:
        return None  # transient network/API hiccup - don't alert on our own errors

    now = datetime.datetime.now().astimezone()
    for event in events:
        start = _event_start(event)
        if start is None:
            continue
        start = start.astimezone()
        minutes_away = (start - now).total_seconds() / 60
        event_id = event.get("id")
        if 0 <= minutes_away <= MEETING_WARNING_MINUTES and event_id not in _warned_event_ids:
            _warned_event_ids.add(event_id)
            title = event.get("summary", "an event")
            return f"Heads up - '{title}' starts in about {round(minutes_away)} minutes."
    return None


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_next_meeting",
            "description": "Reports the user's next upcoming calendar event - e.g. 'what's my next meeting', 'what's next on my calendar'.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_todays_schedule",
            "description": "Reports everything on the user's calendar for today - e.g. 'what's on my schedule today'.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_important_emails",
            "description": "Reports unread emails in the user's inbox - e.g. 'any important emails', 'check my email'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "max_results": {"type": "integer", "description": "Max emails to summarize. Defaults to 5."},
                },
                "required": [],
            },
        },
    },
]

FUNCTIONS = {
    "get_next_meeting": get_next_meeting,
    "get_todays_schedule": get_todays_schedule,
    "check_important_emails": check_important_emails,
}
