"""Regression test for the index route's active_only rule.

Live report 2026-08-04: a vacancy the user had applied to disappeared from
the "Відгукнувся" status filter as soon as NAV closed the listing, which
looked exactly like the row had been deleted. The row was fine — the main
list just hardcoded active_only=True, so INACTIVE rows were invisible there
even when you were explicitly asking for your own application history.

Calls the route function directly with a minimal ASGI scope rather than
going through fastapi.testclient — TestClient needs httpx, and pulling in a
new dependency to check one boolean isn't worth it.
"""

import db
import scoring
from starlette.requests import Request

from web import app as web_app


def _insert(conn, uuid, **overrides):
    row = {
        "uuid": uuid,
        "status": "ACTIVE",
        "title": f"Test job {uuid}",
        "business_name": "Test AS",
        "municipal": "Oslo",
        "county": "Oslo",
        "description": "A description long enough to not be a summary.",
        "employer_name": "Test AS",
        "application_url": "https://example.com",
        "application_due": None,
        "link": "https://example.com",
        "engagement_type": "Fast",
        "extent": "Heltid",
        "sector": None,
    }
    row.update(overrides)
    db.upsert_vacancy_row(conn, row, source="test")


def _setup(tmp_path, monkeypatch):
    """get_conn() is a plain db.connect() call, so patching db.connect is
    enough to point the route at a temp DB. web_app.db is the same module
    object as db here, so the original has to be captured before patching —
    referencing db.connect inside the replacement would recurse."""
    db_path = tmp_path / "test.db"
    real_connect = db.connect
    conn = real_connect(db_path)
    monkeypatch.setattr(web_app.db, "connect", lambda *a, **kw: real_connect(db_path))
    return conn


def _close_listing(conn, uuid):
    """Mirror the real lifecycle: a row is inserted ACTIVE, gets scored, and
    only later goes INACTIVE when the source closes the ad. Inserting an
    INACTIVE row directly would leave score NULL — rescore_all only walks
    scorable (ACTIVE) rows — which is not a state production can reach."""
    conn.execute("UPDATE vacancies SET status = 'INACTIVE' WHERE uuid = ?", (uuid,))
    conn.commit()


def _render(conn, **kwargs) -> str:
    # Calling the route function directly (no FastAPI request cycle) skips
    # the dependency resolution that normally turns the unset user_status
    # param into an empty list — its raw default is a `Query(...)` sentinel
    # object, not a list, which broke iterating over it (live failure while
    # writing this: "TypeError: 'Query' object is not iterable"). Only
    # matters here, in this direct-call test harness; a real HTTP request
    # never hits this path.
    kwargs.setdefault("user_status", [])
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b""})
    return web_app.index(request, **kwargs).body.decode()


def test_applied_vacancy_stays_visible_after_listing_closes(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    _insert(conn, "closed-but-applied")
    db.set_user_status(conn, "closed-but-applied", "applied")
    scoring.rescore_all(conn)
    _close_listing(conn, "closed-but-applied")

    assert "closed-but-applied" in _render(conn, user_status=["applied"])


def test_default_listing_still_hides_closed_vacancies(tmp_path, monkeypatch):
    """The open-listings backlog must not fill up with dead postings — the
    fix above is scoped to reacted-status filters only."""
    conn = _setup(tmp_path, monkeypatch)
    _insert(conn, "closed-untouched")
    _insert(conn, "open-untouched")
    scoring.rescore_all(conn)
    _close_listing(conn, "closed-untouched")

    body = _render(conn)
    assert "open-untouched" in body
    assert "closed-untouched" not in body


def test_new_status_filter_still_hides_closed_vacancies(tmp_path, monkeypatch):
    """user_status="new" IS the open backlog, so it keeps the ACTIVE-only
    rule even though it's an explicit status filter."""
    conn = _setup(tmp_path, monkeypatch)
    _insert(conn, "closed-and-new")
    scoring.rescore_all(conn)
    _close_listing(conn, "closed-and-new")

    assert "closed-and-new" not in _render(conn, user_status=["new"])


def test_multiple_statuses_are_ored_together(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    _insert(conn, "job-interesting")
    db.set_user_status(conn, "job-interesting", "interesting")
    _insert(conn, "job-applied")
    db.set_user_status(conn, "job-applied", "applied")
    _insert(conn, "job-rejected")
    db.set_user_status(conn, "job-rejected", "rejected")
    scoring.rescore_all(conn)

    body = _render(conn, user_status=["interesting", "applied"])
    assert "job-interesting" in body
    assert "job-applied" in body
    assert "job-rejected" not in body


def test_applied_vacancy_hidden_from_default_listing_but_survives_a_closed_source(tmp_path, monkeypatch):
    """Live report 2026-08-16 established that an applied vacancy must never
    vanish just because NAV closed the listing (active_only exemption). User-
    requested 2026-09-02 ("чисто Нове та Цікаво") layered a separate rule on
    top: the *default* (zero-filter) view now only shows "new"/"interesting"
    at all, applied included — same treatment rejected/ignored already had
    since 2026-08-23. Both hold at once: gone from the default view, but
    explicitly filtering to "Відгукнувся" still finds it regardless of the
    listing's own ACTIVE/INACTIVE state — the 2026-08-16 guarantee, just one
    click away instead of zero."""
    conn = _setup(tmp_path, monkeypatch)
    _insert(conn, "closed-but-applied-default")
    db.set_user_status(conn, "closed-but-applied-default", "applied")
    scoring.rescore_all(conn)
    _close_listing(conn, "closed-but-applied-default")

    assert "closed-but-applied-default" not in _render(conn)
    assert "closed-but-applied-default" in _render(conn, user_status=["applied"])


def test_applied_vacancy_findable_by_search_even_though_hidden_from_default_listing(tmp_path, monkeypatch):
    """Live report 2026-09-03: the user could not find a vacancy they had
    already applied to ("Super Micro Computer") by searching its name — the
    2026-09-02 default-status restriction applied to search too, so a
    deliberate lookup was silently narrowed the same as passive browsing.
    User-requested fix: search always looks across every status (still
    excluding "Смітник" — see the next test), independent of the plain-
    listing default; the 2026-08-16 always-findable-after-closing guarantee
    now holds for a bare search with zero status filters, not just an
    explicit "Відгукнувся" filter."""
    conn = _setup(tmp_path, monkeypatch)
    _insert(conn, "closed-but-applied-search", title="Senior IT-konsulent Alstahaug")
    db.set_user_status(conn, "closed-but-applied-search", "applied")
    scoring.rescore_all(conn)
    _close_listing(conn, "closed-but-applied-search")

    assert "closed-but-applied-search" in _render(conn, q="Alstahaug")


def test_archived_vacancy_still_excluded_from_search(tmp_path, monkeypatch):
    """The one status search does NOT reach into by default — "Смітник" rows
    are meant to be gone outright (delete_archived() removes them on the next
    sync); this only matters in the narrow window before that runs."""
    conn = _setup(tmp_path, monkeypatch)
    _insert(conn, "trashed-searchable", title="Senior IT-konsulent Alstahaug")
    db.set_user_status(conn, "trashed-searchable", "archived")
    scoring.rescore_all(conn)

    assert "trashed-searchable" not in _render(conn, q="Alstahaug")
    assert "trashed-searchable" in _render(conn, q="Alstahaug", user_status=["archived"])


def test_interesting_vacancy_still_hidden_by_default_after_listing_closes(tmp_path, monkeypatch):
    """The always-visible exemption deliberately stops at "interesting" —
    it is still unactioned backlog, not a tracked application, per the
    user's own framing ("якщо не має статус Нове або Цікаво")."""
    conn = _setup(tmp_path, monkeypatch)
    _insert(conn, "closed-and-interesting")
    db.set_user_status(conn, "closed-and-interesting", "interesting")
    scoring.rescore_all(conn)
    _close_listing(conn, "closed-and-interesting")

    assert "closed-and-interesting" not in _render(conn)
