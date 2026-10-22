"""Tests for gmail_client.py's IMAP layer — imaplib.IMAP4_SSL mocked out,
no real network/mailbox access. Verifies the full round trip: search ->
per-UID fetch -> MIME parse -> plain-text extraction, against real
constructed RFC822 messages (built with the stdlib email module, same as
what Gmail actually sends over the wire)."""

import email.message
import imaplib
import json

import pytest

import gmail_client as gc


def _raw_email(plain_text: str) -> bytes:
    msg = email.message.EmailMessage()
    msg["From"] = "sender@example.com"
    msg["Subject"] = "Test"
    msg.set_content(plain_text)
    return msg.as_bytes()


class _FakeIMAP:
    """Minimal stand-in for imaplib.IMAP4_SSL - only the methods and
    response shapes fetch_plain_texts() actually calls. FETCH answers the way
    imaplib hands back a multi-message response: a flat list of
    (header, literal) tuples, each followed by a bare b")"."""

    uid_after_literal = False  # servers may put "UID n" after the literal instead of in the header
    reverse_response = False  # response order is not guaranteed to match the request
    batch_fetch_status = "OK"  # status of a FETCH naming more than one UID

    def __init__(self, raw_by_uid):
        self._raw_by_uid = raw_by_uid
        self.logged_in_as = None
        self.fetch_calls = []
        self.select_calls = []

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def login(self, address, password):
        self.logged_in_as = address

    select_status = "OK"

    def select(self, mailbox, readonly=False):
        self.select_calls.append((mailbox, readonly))
        return self.select_status, [b"1"]

    def uid(self, command, *args):
        if command == "search":
            uids = " ".join(str(u) for u in self._raw_by_uid).encode()
            return "OK", [uids]
        if command == "fetch":
            uid_set, items = args
            self.fetch_calls.append((uid_set, items))
            wanted = [int(u) for u in bytes(uid_set).split(b",")]
            if len(wanted) > 1 and self.batch_fetch_status != "OK":
                return self.batch_fetch_status, [b"error"]
            if self.reverse_response:
                wanted.reverse()
            resp = []
            for uid in wanted:
                raw = self._raw_by_uid.get(uid)
                if raw is None:
                    continue
                if self.uid_after_literal:
                    resp += [(b"1 (BODY[] {%d}" % len(raw), raw), b" UID %d)" % uid]
                else:
                    resp += [(b"1 (UID %d BODY[] {%d}" % (uid, len(raw)), raw), b")"]
            return "OK", resp or [None]
        raise AssertionError(f"unexpected UID command: {command}")


def _write_credentials(tmp_path, monkeypatch):
    path = tmp_path / "gmail_app_password.json"
    path.write_text(json.dumps({"email": "you@gmail.com", "app_password": "xxxx xxxx xxxx xxxx"}), encoding="utf-8")
    monkeypatch.setattr(gc, "CREDENTIALS_PATH", path)
    return path


def test_fetch_plain_texts_parses_real_messages(tmp_path, monkeypatch):
    _write_credentials(tmp_path, monkeypatch)
    fake = _FakeIMAP({101: _raw_email("Hello from finn.no"), 102: _raw_email("Другий лист")})
    monkeypatch.setattr(imaplib, "IMAP4_SSL", lambda host, timeout=None: fake)

    texts = gc.fetch_plain_texts("from:finn.no")

    assert len(texts) == 2
    assert "Hello from finn.no" in texts[0]
    assert "Другий лист" in texts[1]
    assert fake.logged_in_as == "you@gmail.com"


def test_fetch_plain_texts_empty_search_returns_empty_list(tmp_path, monkeypatch):
    _write_credentials(tmp_path, monkeypatch)
    monkeypatch.setattr(imaplib, "IMAP4_SSL", lambda host, timeout=None: _FakeIMAP({}))

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

    monkeypatch.setattr(imaplib, "IMAP4_SSL", lambda host, timeout=None: _RejectingIMAP({}))

    try:
        gc.fetch_plain_texts("from:finn.no")
        assert False, "expected GmailAuthError"
    except gc.GmailAuthError as e:
        assert "IMAP login failed" in str(e)


def test_imap_connection_gets_a_timeout(tmp_path, monkeypatch):
    """imaplib has no default timeout — a stalled connection hung the sync
    forever (2026-10-05)."""
    _write_credentials(tmp_path, monkeypatch)
    seen = {}

    def factory(host, timeout=None):
        seen["timeout"] = timeout
        return _FakeIMAP({})

    monkeypatch.setattr(imaplib, "IMAP4_SSL", factory)
    gc.fetch_plain_texts("from:x")
    assert seen["timeout"] == gc.IMAP_TIMEOUT == 60


def test_one_malformed_message_does_not_lose_the_batch(tmp_path, monkeypatch, caplog):
    """A bogus charset makes get_content() raise LookupError; that used to
    abort the whole fetch and drop every digest (2026-10-05)."""
    _write_credentials(tmp_path, monkeypatch)
    bad = (
        b"From: a@example.com\r\nSubject: bad\r\nMIME-Version: 1.0\r\n"
        b"Content-Type: text/plain; charset=bogus-charset-xyz\r\n"
        b"Content-Transfer-Encoding: 8bit\r\n\r\nbody\r\n"
    )
    fake = _FakeIMAP({101: _raw_email("first ok"), 102: bad, 103: _raw_email("third ok")})
    monkeypatch.setattr(imaplib, "IMAP4_SSL", lambda host, timeout=None: fake)

    with caplog.at_level("WARNING"):
        texts = gc.fetch_plain_texts("from:finn.no")

    assert len(texts) == 2
    assert "first ok" in texts[0] and "third ok" in texts[1]
    assert any("Skipping unparseable message" in r.message for r in caplog.records)


def test_failed_select_raises_a_clear_error(tmp_path, monkeypatch):
    _write_credentials(tmp_path, monkeypatch)

    class _NoMailbox(_FakeIMAP):
        select_status = "NO"

    monkeypatch.setattr(imaplib, "IMAP4_SSL", lambda host, timeout=None: _NoMailbox({}))

    try:
        gc.fetch_plain_texts("from:finn.no")
        assert False, "expected GmailMailboxError"
    except gc.GmailMailboxError as e:
        assert "All Mail" in str(e)


def _texts_for(raw_by_uid, monkeypatch, tmp_path, **fake_attrs):
    _write_credentials(tmp_path, monkeypatch)
    fake = _FakeIMAP(raw_by_uid)
    for k, v in fake_attrs.items():
        setattr(fake, k, v)
    monkeypatch.setattr(imaplib, "IMAP4_SSL", lambda host, timeout=None: fake)
    return gc.fetch_plain_texts("from:finn.no"), fake


def test_messages_are_fetched_in_batches_not_one_round_trip_each(tmp_path, monkeypatch):
    """60 digests used to be 60 FETCH round trips (~0.4 s each); now one per
    FETCH_BATCH_SIZE UIDs, still oldest first, and still read-only."""
    raw = {1000 + i: _raw_email(f"message {i}") for i in range(2 * gc.FETCH_BATCH_SIZE + 7)}

    texts, fake = _texts_for(raw, monkeypatch, tmp_path)

    assert [t.strip() for t in texts] == [f"message {i}" for i in range(len(raw))]
    assert len(fake.fetch_calls) == 3
    assert all(items == "(UID BODY.PEEK[])" for _, items in fake.fetch_calls), "must never mark mail as read"
    assert fake.select_calls == [('"[Gmail]/All Mail"', True)]


def test_batch_responses_are_matched_by_uid_not_by_position(tmp_path, monkeypatch):
    raw = {5: _raw_email("five"), 6: _raw_email("six"), 7: _raw_email("seven")}

    texts, _ = _texts_for(raw, monkeypatch, tmp_path, reverse_response=True)
    assert [t.strip() for t in texts] == ["five", "six", "seven"]

    texts, _ = _texts_for(raw, monkeypatch, tmp_path, uid_after_literal=True)
    assert [t.strip() for t in texts] == ["five", "six", "seven"]


def test_a_uid_the_server_returns_nothing_for_is_skipped(tmp_path, monkeypatch):
    class _Vanishing(_FakeIMAP):
        def uid(self, command, *args):
            if command == "search":
                return "OK", [b"1 2 3"]
            return super().uid(command, *args)

    _write_credentials(tmp_path, monkeypatch)
    fake = _Vanishing({1: _raw_email("one"), 3: _raw_email("three")})  # 2 was expunged
    monkeypatch.setattr(imaplib, "IMAP4_SSL", lambda host, timeout=None: fake)

    assert [t.strip() for t in gc.fetch_plain_texts("from:x")] == ["one", "three"]


def test_failed_batch_fetch_falls_back_to_one_by_one(tmp_path, monkeypatch):
    raw = {1: _raw_email("one"), 2: _raw_email("two"), 3: _raw_email("three")}

    texts, fake = _texts_for(raw, monkeypatch, tmp_path, batch_fetch_status="NO")

    assert [t.strip() for t in texts] == ["one", "two", "three"]
    assert len(fake.fetch_calls) == 1 + 3


def test_missing_credentials_message_has_no_absolute_path(tmp_path, monkeypatch):
    """The message lands in the sync summary shown in the UI: it must not
    leak the drive letter / home dir (Windows username) of the checkout."""
    monkeypatch.setattr(gc, "CREDENTIALS_PATH", tmp_path / "gmail_app_password.json")
    with pytest.raises(gc.GmailAuthError) as exc:
        gc.fetch_plain_texts("from:finn.no")
    msg = str(exc.value)
    assert str(tmp_path) not in msg and str(tmp_path.anchor) not in msg
    assert "Users" not in msg and "gmail_app_password.json" in msg
