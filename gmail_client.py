"""Gmail access via IMAP + an app password — not OAuth.

Google forces an OAuth refresh token to expire every 7 days for an
unverified app ("Testing" publishing status) requesting a restricted scope
like gmail.readonly, and full verification requires a paid third-party
security audit — not viable for a personal project. Confirmed live
2026-08-26: the token expired exactly 7 days after being reissued, for the
third time. An app password (myaccount.google.com/apppasswords, requires
2-Step Verification) doesn't expire on its own — only on password change or
manual revocation — and Gmail's IMAP server accepts the same search syntax
as the Gmail search box via the X-GM-RAW extension, so every existing
"from:X" query keeps working unchanged. See jobsearch-norway-sources
memory for the full OAuth-vs-IMAP investigation.
"""

import email
import imaplib
import json
from email.policy import default as email_policy
from pathlib import Path

CREDENTIALS_PATH = Path(__file__).parent / "credentials" / "gmail_app_password.json"
IMAP_HOST = "imap.gmail.com"


class GmailAuthError(Exception):
    """Raised when credentials/gmail_app_password.json is missing, or the
    IMAP login itself fails (wrong password, 2-Step Verification not
    enabled, app password revoked). Fix: generate a fresh app password at
    myaccount.google.com/apppasswords and write it to that file (see
    _load_credentials()'s error message for the exact JSON shape) —
    credentials/ is entirely gitignored, same as the old client_secret.json,
    so there's no tracked .example to copy from."""


def _load_credentials() -> tuple[str, str]:
    if not CREDENTIALS_PATH.exists():
        raise GmailAuthError(
            f'No Gmail app-password file at {CREDENTIALS_PATH}. Create it with:\n'
            f'{{"email": "you@gmail.com", "app_password": "xxxx xxxx xxxx xxxx"}}\n'
            f"(app_password from myaccount.google.com/apppasswords, requires "
            f"2-Step Verification enabled on that account)."
        )
    data = json.loads(CREDENTIALS_PATH.read_text(encoding="utf-8"))
    return data["email"], data["app_password"]


def fetch_plain_texts(query: str) -> list[str]:
    """Runs a Gmail-syntax search (the IMAP X-GM-RAW extension — accepts the
    exact same query language as the Gmail search box, e.g. "from:finn.no")
    against the inbox and returns the plain-text body of every match, most
    recent last. Read-only: the mailbox's own read/unread state is never
    touched (Gmail's IMAP server marks messages read on FETCH by default;
    fetching BODY.PEEK[] instead of RFC822 avoids that side effect)."""
    address, password = _load_credentials()
    texts = []
    with imaplib.IMAP4_SSL(IMAP_HOST) as imap:
        try:
            imap.login(address, password)
        except imaplib.IMAP4.error as e:
            raise GmailAuthError(f"IMAP login failed: {e}") from e
        imap.select("INBOX", readonly=True)
        status, data = imap.uid("search", "X-GM-RAW", f'"{query}"')
        if status != "OK" or not data or not data[0]:
            return texts
        for uid in data[0].split():
            status, msg_data = imap.uid("fetch", uid, "(BODY.PEEK[])")
            if status != "OK" or not msg_data or not msg_data[0]:
                continue
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw, policy=email_policy)
            body = msg.get_body(preferencelist=("plain",))
            if body is not None:
                texts.append(body.get_content())
    return texts
