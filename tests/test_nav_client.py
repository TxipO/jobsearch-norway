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
    # "a" was re-sent byte-identical, so it is "unchanged", not "updated"
    # (2026-10-05: idle syncs used to report updated=N forever).
    assert second["updated"] == 0 and second["unchanged"] == 1
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
    assert (second["new"], second["updated"], second["unchanged"]) == (1, 0, 2)


def test_idle_sync_reports_unchanged_not_updated(tmp_path, monkeypatch):
    """2026-10-05: the tip page is re-read every sync, so an idle day used to
    print updated=N forever. Only a real content change counts as updated."""
    pages = {"p1": {"items": [_entry("a")], "etag": "tip"}}
    _install_feed(monkeypatch, pages)
    conn = db.connect(tmp_path / "t.db")
    db.set_state(conn, nav_client.CURSOR_KEY, "p1")
    nav_client.sync(conn)

    idle = nav_client.sync(conn)
    assert (idle["new"], idle["updated"], idle["unchanged"]) == (0, 0, 1)

    # Same uuid, different content on the feedentry endpoint -> updated.
    def changed_get(url, headers=None, timeout=None):
        if "/feedentry/" in url:
            return _Resp(200, {"ad_content": {"title": "Ad v2", "description": "d"}})
        return _Resp(200, {"id": "p1", "items": [_entry("a")], "next_id": None})
    monkeypatch.setattr(nav_client.requests, "get", changed_get)
    changed = nav_client.sync(conn)
    assert (changed["updated"], changed["unchanged"]) == (1, 0)


def _install_flaky_feed(monkeypatch, pages, failing, exc_factory):
    """Like _install_feed, but detail fetches for uuids in `failing` raise."""
    def fake_get(url, headers=None, timeout=None):
        if "/feedentry/" in url:
            uuid = url.rsplit("/", 1)[-1]
            if uuid in failing:
                raise exc_factory()
            return _Resp(200, {"ad_content": {"title": "Ad " + uuid, "description": "d"}})
        page_id = url.rsplit("/", 1)[-1]
        page = pages[page_id]
        return _Resp(200, {"id": page_id, "items": page["items"], "next_id": page.get("next_id")})

    monkeypatch.setattr(nav_client.requests, "get", fake_get)
    monkeypatch.setattr(nav_client, "get_token", lambda: "tok")


def test_transient_detail_failure_holds_the_cursor_on_that_page(tmp_path, monkeypatch):
    """Reproduced 2026-10-05: a transient detail-fetch error only bumped
    detail_missing, then the cursor advanced past the sealed page and the ad
    was lost forever. The cursor must stay on the page so the next sync
    retries it, and the failure must be visible in stats."""
    pages = {
        "p1": {"items": [_entry("a"), _entry("b")], "next_id": "p2"},
        "p2": {"items": [_entry("c")], "etag": "tip"},
    }
    failing = {"b"}
    _install_flaky_feed(monkeypatch, pages, failing, lambda: nav_client.requests.ConnectionError("boom"))
    conn = db.connect(tmp_path / "t.db")
    db.set_state(conn, nav_client.CURSOR_KEY, "p1")

    stats = nav_client.sync(conn)

    assert db.get_state(conn, nav_client.CURSOR_KEY) == "p1"
    assert stats["detail_errors"] == 1
    assert stats["pages"] == 1, "must not walk past the failed page"
    assert {r[0] for r in conn.execute("SELECT uuid FROM vacancies")} == {"a"}

    # Network recovers: the retry picks up b and walks on to the tip.
    failing.clear()
    stats = nav_client.sync(conn)
    assert stats["detail_errors"] == 0
    assert {r[0] for r in conn.execute("SELECT uuid FROM vacancies")} == {"a", "b", "c"}
    assert db.get_state(conn, nav_client.CURSOR_KEY) == "p2"


def test_permanent_4xx_detail_error_does_not_wedge_the_cursor(tmp_path, monkeypatch):
    """A withdrawn ad answers 404 forever; holding the cursor for it would
    stall the feed permanently. Only transient errors hold the cursor."""
    pages = {
        "p1": {"items": [_entry("a")], "next_id": "p2"},
        "p2": {"items": [_entry("c")], "etag": "tip"},
    }

    def gone():
        resp = nav_client.requests.Response()
        resp.status_code = 404
        return nav_client.requests.HTTPError("404", response=resp)

    _install_flaky_feed(monkeypatch, pages, {"a"}, gone)
    conn = db.connect(tmp_path / "t.db")
    db.set_state(conn, nav_client.CURSOR_KEY, "p1")

    stats = nav_client.sync(conn)

    assert stats["detail_missing"] == 1 and stats["detail_errors"] == 0
    assert db.get_state(conn, nav_client.CURSOR_KEY) == "p2"


def test_detail_without_content_is_a_skip_and_the_cursor_advances(tmp_path, monkeypatch):
    pages = {
        "p1": {"items": [_entry("a")], "next_id": "p2"},
        "p2": {"items": [], "etag": "tip"},
    }

    def fake_get(url, headers=None, timeout=None):
        if "/feedentry/" in url:
            return _Resp(200, {"ad_content": None})
        page_id = url.rsplit("/", 1)[-1]
        return _Resp(200, {"id": page_id, "items": pages[page_id]["items"],
                           "next_id": pages[page_id].get("next_id")})

    monkeypatch.setattr(nav_client.requests, "get", fake_get)
    monkeypatch.setattr(nav_client, "get_token", lambda: "tok")
    conn = db.connect(tmp_path / "t.db")
    db.set_state(conn, nav_client.CURSOR_KEY, "p1")

    stats = nav_client.sync(conn)

    assert stats["detail_missing"] == 1 and stats["detail_errors"] == 0
    assert db.get_state(conn, nav_client.CURSOR_KEY) == "p2"


def test_inactive_entry_with_missing_keys_does_not_wedge_the_cursor(tmp_path, monkeypatch):
    """An INACTIVE feed entry lacking title/businessName/municipal used to
    raise KeyError before the cursor moved — wedged forever."""
    bare = {"_feed_entry": {"uuid": "x", "status": "INACTIVE"}}
    pages = {
        "p1": {"items": [bare], "next_id": "p2"},
        "p2": {"items": [], "etag": "tip"},
    }
    _install_feed(monkeypatch, pages)
    conn = db.connect(tmp_path / "t.db")
    db.set_state(conn, nav_client.CURSOR_KEY, "p1")

    stats = nav_client.sync(conn)

    assert stats["marked_inactive"] == 1
    assert db.get_state(conn, nav_client.CURSOR_KEY) == "p2"


def _flaky_two_pages(monkeypatch, failing, exc_factory):
    pages = {
        "p1": {"items": [_entry("a"), _entry("b")], "next_id": "p2"},
        "p2": {"items": [_entry("c")], "etag": "tip"},
    }
    _install_flaky_feed(monkeypatch, pages, failing, exc_factory)


def test_persistently_failing_ad_holds_cursor_three_syncs_then_advances(tmp_path, monkeypatch):
    """Review 2026-10-05: with no retry cap one ad that always 5xx-ed held
    the cursor on its page forever (2026-08-31 stall shape)."""
    failing = {"b"}
    _flaky_two_pages(monkeypatch, failing, lambda: nav_client.requests.ConnectionError("boom"))
    conn = db.connect(tmp_path / "t.db")
    db.set_state(conn, nav_client.CURSOR_KEY, "p1")

    for n in (1, 2, 3):
        stats = nav_client.sync(conn)
        assert db.get_state(conn, nav_client.CURSOR_KEY) == "p1", f"sync {n} must hold"
        assert stats["detail_errors"] == 1 and stats["detail_missing"] == 0

    stats = nav_client.sync(conn)
    assert stats["detail_missing"] == 1 and stats["detail_errors"] == 0
    assert db.get_state(conn, nav_client.CURSOR_KEY) == "p2"
    assert {r[0] for r in conn.execute("SELECT uuid FROM vacancies")} == {"a", "c"}
    # Passing the page prunes the counter.
    assert "b" not in __import__("json").loads(db.get_state(conn, nav_client.FAILURES_KEY))


def test_invalid_json_detail_body_is_transient_but_capped(tmp_path, monkeypatch):
    import json
    failing = {"b"}
    _flaky_two_pages(monkeypatch, failing, lambda: json.JSONDecodeError("bad", "", 0))
    conn = db.connect(tmp_path / "t.db")
    db.set_state(conn, nav_client.CURSOR_KEY, "p1")

    first = nav_client.sync(conn)
    assert first["detail_errors"] == 1
    assert db.get_state(conn, nav_client.CURSOR_KEY) == "p1"
    for _ in range(2):
        nav_client.sync(conn)
    assert db.get_state(conn, nav_client.CURSOR_KEY) == "p1"
    last = nav_client.sync(conn)
    assert last["detail_missing"] == 1
    assert db.get_state(conn, nav_client.CURSOR_KEY) == "p2"


def test_recovery_clears_the_failure_counter(tmp_path, monkeypatch):
    import json
    failing = {"b"}
    _flaky_two_pages(monkeypatch, failing, lambda: nav_client.requests.ConnectionError("boom"))
    conn = db.connect(tmp_path / "t.db")
    db.set_state(conn, nav_client.CURSOR_KEY, "p1")

    nav_client.sync(conn)
    nav_client.sync(conn)
    assert json.loads(db.get_state(conn, nav_client.FAILURES_KEY)) == {"b": 2}

    failing.clear()
    nav_client.sync(conn)
    assert json.loads(db.get_state(conn, nav_client.FAILURES_KEY)) == {}
    assert db.get_state(conn, nav_client.CURSOR_KEY) == "p2"

    # A later, separate outage starts counting from zero again.
    failing.add("c")
    nav_client.sync(conn)
    assert json.loads(db.get_state(conn, nav_client.FAILURES_KEY)) == {"c": 1}
