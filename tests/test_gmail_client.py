"""Tests for gmail_client.py's IMAP layer — imaplib.IMAP4_SSL mocked out,
no real network/mailbox access. Verifies the full round trip: search ->
per-UID fetch -> MIME parse -> plain-text extraction, against real
constructed RFC822 messages (built with the stdlib email module, same as
what Gmail actually sends over the wire)."""

import email.message
import imaplib
import json

import gmail_client as gc


def _raw_email(plain_text: str) -> bytes:
    msg = email.message.EmailMessage()
    msg["From"] = "sender@example.com"
    msg["Subject"] = "Test"
    msg.set_content(plain_text)
    return msg.as_bytes()


class _FakeIMAP:
    """Minimal stand-in for imaplib.IMAP4_SSL — only the methods and
    response shapes fetch_plain_texts() actually calls."""

    def __init__(self, raw_by_uid):
        self._raw_by_uid = raw_by_uid
        self.logged_in_as = None

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def login(self, address, password):
        self.logged_in_as = address

    def select(self, mailbox, readonly=False):
        pass

    def uid(self, command, *args):
        if command == "search":
            uids = " ".join(str(u) for u in self._raw_by_uid).encode()
            return "OK", [uids]
        if command == "fetch":
            raw = self._raw_by_uid.get(int(args[0]))
            if raw is None:
                return "OK", [None]
            return "OK", [(b"1 (UID %d BODY[] {%d}" % (int(args[0]), len(raw)), raw)]
        raise AssertionError(f"unexpected UID command: {command}")


def _write_credentials(tmp_path, monkeypatch):
    path = tmp_path / "gmail_app_password.json"
    path.write_text(json.dumps({"email": "you@gmail.com", "app_password": "xxxx xxxx xxxx xxxx"}), encoding="utf-8")
    monkeypatch.setattr(gc, "CREDENTIALS_PATH", path)
    return path


def test_fetch_plain_texts_parses_real_messages(tmp_path, monkeypatch):
    _write_credentials(tmp_path, monkeypatch)
    fake = _FakeIMAP({101: _raw_email("Hello from finn.no"), 102: _raw_email("Другий лист")})
    monkeypatch.setattr(imaplib, "IMAP4_SSL", lambda host: fake)

    texts = gc.fetch_plain_texts("from:finn.no")

    assert len(texts) == 2
    assert "Hello from finn.no" in texts[0]
    assert "Другий лист" in texts[1]
    assert fake.logged_in_as == "you@gmail.com"


def test_fetch_plain_texts_empty_search_returns_empty_list(tmp_path, monkeypatch):
    _write_credentials(tmp_path, monkeypatch)
    monkeypatch.setattr(imaplib, "IMAP4_SSL", lambda host: _FakeIMAP({}))

    assert gc.fetch_plain_texts("from:nobody") == []


def test_missing_credentials_file_raises_gmail_auth_error(tmp_path, monkeypatch):
    monkeypatch.setattr(gc, "CREDENTIALS_PATH", tmp_path / "does_not_exist.json")

    try:
        gc.fetch_plain_texts("from:finn.no")
        assert False, "expected GmailAuthError"
    except gc.GmailAuthError as e:
        assert "app_password" in str(e)


def test_login_failure_raises_gmail_auth_error(tmp_path, monkeypatch):
    _write_credentials(tmp_path, monkeypatch)

    class _RejectingIMAP(_FakeIMAP):
        def login(self, address, password):
            raise imaplib.IMAP4.error("[AUTHENTICATIONFAILED] Invalid credentials")

    monkeypatch.setattr(imaplib, "IMAP4_SSL", lambda host: _RejectingIMAP({}))

    try:
        gc.fetch_plain_texts("from:finn.no")
        assert False, "expected GmailAuthError"
    except gc.GmailAuthError as e:
        assert "IMAP login failed" in str(e)
