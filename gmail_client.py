import base64
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
CREDENTIALS_PATH = Path(__file__).parent / "credentials" / "client_secret.json"
TOKEN_PATH = Path(__file__).parent / "data" / "gmail_token.json"


class GmailAuthError(Exception):
    """Raised when the stored token can't be silently refreshed. The fix is
    always the same: run `py -c "from gmail_client import setup; setup()"`
    once from an interactive terminal — never from the web server."""


def get_service():
    """Web/sync-route-safe: refreshes an expired token silently, but never
    launches the interactive OAuth flow. Live bug 2026-07-17 ("Sync now"
    button hanging forever, inconsistently): a genuinely invalid/missing
    token used to fall through to flow.run_local_server(port=0), which
    opens a local HTTP server and BLOCKS waiting for someone to complete a
    browser consent flow. Called from inside a FastAPI request handler,
    that's not a slow request — it's a request that never returns, tying up
    a thread indefinitely with no one watching for the browser prompt."""
    if not TOKEN_PATH.exists():
        raise GmailAuthError("No Gmail token on disk yet — run gmail_client.setup() once, interactively.")

    creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            TOKEN_PATH.write_text(creds.to_json())
        else:
            raise GmailAuthError(
                "Gmail token is invalid and has no refresh_token — run gmail_client.setup() again."
            )

    return build("gmail", "v1", credentials=creds)


def setup():
    """Interactive, first-time (or re-)authorization — run this yourself
    from a terminal, never call it from the web app. Opens a browser."""
    flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
    creds = flow.run_local_server(port=0)
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(creds.to_json())
    return creds


def search_messages(service, query: str, max_results: int = 50) -> list[dict]:
    messages = []
    request = service.users().messages().list(userId="me", q=query, maxResults=max_results)
    while request is not None:
        response = request.execute()
        messages.extend(response.get("messages", []))
        if len(messages) >= max_results:
            break
        request = service.users().messages().list_next(request, response)
    return messages[:max_results]


def get_message(service, message_id: str) -> dict:
    return service.users().messages().get(userId="me", id=message_id, format="full").execute()


def _walk_parts(payload: dict):
    if payload.get("parts"):
        for part in payload["parts"]:
            yield from _walk_parts(part)
    else:
        yield payload


def extract_bodies(message: dict) -> dict:
    """Returns {'text/plain': str, 'text/html': str} for whatever parts are present."""
    bodies = {}
    for part in _walk_parts(message["payload"]):
        mime_type = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if data and mime_type in ("text/plain", "text/html"):
            bodies[mime_type] = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
    return bodies
