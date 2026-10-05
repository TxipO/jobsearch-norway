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
import logging
from email.policy import default as email_policy
from pathlib import Path

CREDENTIALS_PATH = Path(__file__).parent / "credentials" / "gmail_app_password.json"
IMAP_HOST = "imap.gmail.com"
# Socket timeout (seconds) for the IMAP connection. imaplib's default is no
# timeout at all, so a stalled connection hung the whole sync (and with it the
# web /sync request) forever — 2026-10-05 audit.
IMAP_TIMEOUT = 60

logger = logging.getLogger(__name__)


class GmailMailboxError(Exception):
    """Raised when "[Gmail]/All Mail" can't be selected — most often because
    the account's Gmail UI language renames the folder (e.g. "[Gmail]/All
    Mail" is localized on some accounts) or IMAP access for it is hidden in
    Gmail settings (Labels -> "Show in IMAP")."""


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
    against the whole mailbox and returns the plain-text body of every
    match, most recent last. Selects "[Gmail]/All Mail", not INBOX: a
    message that's been archived or moved under a label (e.g. the user's
    own "WorkSpam" label on finn.no/LinkedIn digests) leaves INBOX entirely
    — IMAP SEARCH only searches within the currently selected mailbox, so
    selecting INBOX silently missed every archived digest even though the
    equivalent OAuth-based Gmail API search (which searches all mail by
    default) found them (live bug, 2026-08-26: 0 results from a mailbox
    that actually had 13/49 matching messages). Read-only: the mailbox's
    own read/unread state is never touched (Gmail's IMAP server marks
    messages read on FETCH by default; fetching BODY.PEEK[] instead of
    RFC822 avoids that side effect)."""
    address, password = _load_credentials()
    texts = []
    with imaplib.IMAP4_SSL(IMAP_HOST, timeout=IMAP_TIMEOUT) as imap:
        try:
            imap.login(address, password)
        except imaplib.IMAP4.error as e:
            raise GmailAuthError(f"IMAP login failed: {e}") from e
        status, data = imap.select('"[Gmail]/All Mail"', readonly=True)
        if status != "OK":
            # Without this check a failed select fell through to SEARCH in the
            # wrong state and surfaced as an unrelated imaplib error (or a
            # silent empty result) — 2026-10-05 audit.
            raise GmailMailboxError(
                f'Could not select "[Gmail]/All Mail" (status {status}: {data}). '
                f"Check the folder name / IMAP visibility in Gmail settings."
            )
        status, data = imap.uid("search", "X-GM-RAW", f'"{query}"')
        if status != "OK" or not data or not data[0]:
            return texts
        for uid in data[0].split():
            status, msg_data = imap.uid("fetch", uid, "(BODY.PEEK[])")
            if status != "OK" or not msg_data or not msg_data[0]:
                continue
            # One malformed message (bogus charset -> LookupError from
            # get_content, bad MIME structure, ...) used to abort the whole
            # batch and lose every other digest with it — skip just that
            # message (2026-10-05 audit).
            try:
                raw = msg_data[0][1]
                msg = email.message_from_bytes(raw, policy=email_policy)
                body = msg.get_body(preferencelist=("plain",))
                if body is not None:
                    texts.append(body.get_content())
            except (LookupError, ValueError, UnicodeError, IndexError, TypeError) as e:
                logger.warning(f"Skipping unparseable message UID {uid!r}: {e!r}")
                continue
    return texts
