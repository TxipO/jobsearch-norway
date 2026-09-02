"""Regression tests for the NAV feed cursor.

Live incident found 2026-09-02: NAV had ingested nothing since 2026-08-31
while every sync in between reported "+0 new / -0 deactivated" — twelve
sealed pages and 10 375 entries had queued up behind a frozen cursor.

Two root causes, both fixed here:

1. The cursor was left on the page just *read* rather than the page still to
   be read, so any failure while fetching the following page froze it there.
2. It was re-requested with `If-None-Match`. NAV's ETag is not a content
   hash — it is the id of the page that follows (measured live: every page's
   ETag equals its own next_id, and it does not change as entries are
   appended). So the ETag answered 304 while genuinely new ads sat behind it,
   which made the frozen cursor permanent *and* silent. Conditional requests
   are gone; the cursor page is always read.

There were no nav_client tests at all before this, which is why it survived
days of daily use.
"""

import db
import nav_client


def _entry(uuid, status="ACTIVE", title="T"):
    return {
        "_feed_entry": {
            "uuid": uuid,
            "status": status,
            "title": title,
            "businessName": "B",
            "municipal": "OSLO",
        }
    }


class _Resp:
    def __init__(self, status_code, payload=None, etag=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = {"ETag": etag} if etag else {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"unexpected HTTP {self.status_code}")


def _install_feed(monkeypatch, pages):
    """Serve `pages` (id -> {items, next_id}) the way NAV does, including the
    trap: a page's ETag IS its next_id, and honouring `If-None-Match` yields
    304 even when the feed has moved on. Any test that starts passing only
    because conditional requests came back will fail here."""
    requested = []

    def fake_get(url, headers=None, timeout=None):
        if "/feedentry/" in url:
            return _Resp(200, {"ad_content": {"title": "Ad", "description": "d"}})
        page_id = url.rsplit("/", 1)[-1]
        requested.append(page_id)
        page = pages[page_id]
        etag = page.get("next_id") or page.get("etag")
        if headers and headers.get("If-None-Match") == etag:
            return _Resp(304, etag=etag)
        payload = {"id": page_id, "items": page["items"], "next_id": page.get("next_id")}
        return _Resp(200, payload, etag=etag)

    monkeypatch.setattr(nav_client.requests, "get", fake_get)
    monkeypatch.setattr(nav_client, "get_token", lambda: "tok")
    return requested


def test_walks_the_whole_chain_to_the_tip(tmp_path, monkeypatch):
    pages = {
        "p1": {"items": [_entry("a")], "next_id": "p2"},
        "p2": {"items": [_entry("b")], "next_id": "p3"},
        "p3": {"items": [_entry("c")], "etag": "tip-1"},
    }
    requested = _install_feed(monkeypatch, pages)

    conn = db.connect(tmp_path / "t.db")
    db.set_state(conn, nav_client.CURSOR_KEY, "p1")

    stats = nav_client.sync(conn)

    assert requested == ["p1", "p2", "p3"]
    assert stats["pages"] == 3
    assert stats["new"] == 3
    assert {r[0] for r in conn.execute("SELECT uuid FROM vacancies")} == {"a", "b", "c"}
    assert db.get_state(conn, nav_client.CURSOR_KEY) == "p3"


def test_cursor_never_rests_on_a_consumed_page(tmp_path, monkeypatch):
    """The invariant behind the incident. If the cursor is left on a page that
    already has a successor, a single failed fetch of that successor strands
    the feed — the cursor must name the page still to be read."""
    pages = {
        "p1": {"items": [_entry("a")], "next_id": "p2"},
        "p2": {"items": [_entry("b")], "etag": "tip-1"},
    }
    _install_feed(monkeypatch, pages)
    conn = db.connect(tmp_path / "t.db")
    db.set_state(conn, nav_client.CURSOR_KEY, "p1")

    nav_client.sync(conn)

    cursor = db.get_state(conn, nav_client.CURSOR_KEY)
    assert pages[cursor].get("next_id") is None


def test_stranded_cursor_from_an_interrupted_run_resumes(tmp_path, monkeypatch):
    """Exactly the state the incident left behind: cursor sitting on a sealed
    page. It must walk on, not report an empty sync."""
    pages = {
        "p1": {"items": [_entry("a")], "next_id": "p2"},
        "p2": {"items": [_entry("b")], "next_id": "p3"},
        "p3": {"items": [_entry("c")], "etag": "tip-1"},
    }
    _install_feed(monkeypatch, pages)
    conn = db.connect(tmp_path / "t.db")
    db.set_state(conn, nav_client.CURSOR_KEY, "p1")
    # A leftover ETag from the old implementation must not resurrect 304s.
    db.set_state(conn, nav_client.ETAG_KEY, "p2")

    stats = nav_client.sync(conn)

    assert stats["pages"] == 3 and stats["new"] == 3
    assert db.get_state(conn, nav_client.CURSOR_KEY) == "p3"


def test_new_entries_on_the_tip_page_are_picked_up(tmp_path, monkeypatch):
    """The silent half of the bug. The tip page keeps its id and its ETag
    while ads are appended to it, so a conditional request answers 304 and the
    new ads are never seen. Reading unconditionally is what makes them show
    up."""
    pages = {"p1": {"items": [_entry("a")], "etag": "tip-1"}}
    _install_feed(monkeypatch, pages)
    conn = db.connect(tmp_path / "t.db")
    db.set_state(conn, nav_client.CURSOR_KEY, "p1")

    first = nav_client.sync(conn)
    assert (first["new"], first["updated"]) == (1, 0)

    # Same page id, same ETag — only the contents grew.
    pages["p1"]["items"] = [_entry("a"), _entry("b")]

    second = nav_client.sync(conn)
    assert second["new"] == 1, "a new ad appended to the tip page must be seen"
    assert second["updated"] == 1
    assert {r[0] for r in conn.execute("SELECT uuid FROM vacancies")} == {"a", "b"}


def test_last_state_on_a_page_wins_for_a_repeated_uuid(tmp_path, monkeypatch):
    """A vacancy can be published and withdrawn within one page. The old code
    applied every occurrence AND ran all the ACTIVE upserts after all the
    inactive ones regardless of order, so this page left the ad ACTIVE — the
    opposite of what the feed said."""
    pages = {"p1": {"items": [_entry("a", "ACTIVE"), _entry("a", "INACTIVE")], "etag": "tip"}}
    _install_feed(monkeypatch, pages)
    conn = db.connect(tmp_path / "t.db")
    db.set_state(conn, nav_client.CURSOR_KEY, "p1")

    stats = nav_client.sync(conn)

    status = conn.execute("SELECT status FROM vacancies WHERE uuid = 'a'").fetchone()[0]
    assert status == "INACTIVE"
    assert stats["marked_inactive"] == 1
    assert stats["new"] == 0 and stats["updated"] == 0


def test_new_and_updated_are_counted_separately(tmp_path, monkeypatch):
    """The reported symptom: one lumped "нових/оновлених" number could not tell
    a genuinely new ad from one the feed merely re-sent, so a stalled feed and
    a busy one printed the same kind of number."""
    pages = {"p1": {"items": [_entry("a"), _entry("b")], "etag": "tip"}}
    _install_feed(monkeypatch, pages)
    conn = db.connect(tmp_path / "t.db")
    db.set_state(conn, nav_client.CURSOR_KEY, "p1")

    first = nav_client.sync(conn)
    assert (first["new"], first["updated"]) == (2, 0)

    pages["p1"]["items"] = [_entry("a"), _entry("b"), _entry("c")]
    second = nav_client.sync(conn)
    assert (second["new"], second["updated"]) == (1, 2)
