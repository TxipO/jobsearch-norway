"""Regression tests for the web-layer security fixes from /fullreview deep,
2026-10-05: local-only guard middleware (DNS rebinding + cross-site POST),
href scheme allowlist, open redirect, status-route 400, sync error handling.

No fastapi.testclient (needs httpx, not a dependency here) — the middleware
is exercised by driving the ASGI app directly.
"""

import asyncio
import json

import db
import pytest
from starlette.requests import Request

from web import app as web_app


def _asgi(method, path, headers=None, body=b""):
    """Minimal ASGI round trip -> (status, response_body_bytes)."""
    hdrs = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    if body:
        hdrs.append((b"content-type", b"application/x-www-form-urlencoded"))
        hdrs.append((b"content-length", str(len(body)).encode()))
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
        "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"",
        "headers": hdrs, "server": ("127.0.0.1", 8000), "client": ("127.0.0.1", 50000),
    }
    out = {"status": None, "body": b""}

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(msg):
        if msg["type"] == "http.response.start":
            out["status"] = msg["status"]
        elif msg["type"] == "http.response.body":
            out["body"] += msg.get("body", b"")

    asyncio.run(web_app.app(scope, receive, send))
    return out["status"], out["body"]


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    real_connect = db.connect
    conn = real_connect(db_path)
    monkeypatch.setattr(web_app.db, "connect", lambda *a, **kw: real_connect(db_path))
    return conn


def _insert(conn, uuid="v1"):
    db.upsert_vacancy_row(conn, {
        "uuid": uuid, "status": "ACTIVE", "title": "T", "business_name": "B", "municipal": "Oslo",
        "county": "Oslo", "description": "d" * 80, "employer_name": "B", "application_url": None,
        "application_due": None, "link": None, "engagement_type": None, "extent": None, "sector": None,
    }, source="test")


HOST = {"Host": "127.0.0.1:8000"}


def test_bad_host_header_rejected(tmp_db):
    status, _ = _asgi("GET", "/sync-status", {"Host": "attacker.example:8000"})
    assert status == 400


def test_allowed_hosts_pass(tmp_db):
    for host in ("127.0.0.1:8000", "localhost:8000", "testserver"):
        status, _ = _asgi("GET", "/sync-status", {"Host": host})
        assert status == 200, host


def test_cross_origin_post_rejected_with_403(tmp_db):
    _insert(tmp_db)
    status, _ = _asgi("POST", "/vacancy/v1/flag", {**HOST, "Origin": "https://evil.example"})
    assert status == 403
    assert tmp_db.execute("SELECT flagged_at FROM vacancies WHERE uuid='v1'").fetchone()[0] is None


def test_cross_origin_referer_only_post_rejected(tmp_db):
    _insert(tmp_db)
    status, _ = _asgi("POST", "/vacancy/v1/flag", {**HOST, "Referer": "https://evil.example/page"})
    assert status == 403


def test_null_origin_rejected(tmp_db):
    _insert(tmp_db)
    status, _ = _asgi("POST", "/vacancy/v1/flag", {**HOST, "Origin": "null"})
    assert status == 403


def test_same_origin_post_ok(tmp_db):
    _insert(tmp_db)
    status, _ = _asgi("POST", "/vacancy/v1/flag", {**HOST, "Origin": "http://127.0.0.1:8000"})
    assert status == 200
    assert tmp_db.execute("SELECT flagged_at FROM vacancies WHERE uuid='v1'").fetchone()[0] is not None


def test_same_origin_referer_post_ok(tmp_db):
    _insert(tmp_db)
    status, _ = _asgi("POST", "/vacancy/v1/flag", {**HOST, "Referer": "http://127.0.0.1:8000/vacancy/v1"})
    assert status == 200


def test_post_without_origin_or_referer_allowed_for_cli(tmp_db):
    _insert(tmp_db)
    status, _ = _asgi("POST", "/vacancy/v1/flag", HOST)
    assert status == 200


def test_safe_url_allowlist():
    f = web_app.safe_url
    assert f("https://example.com/a?b=1") == "https://example.com/a?b=1"
    assert f("HTTP://example.com") == "HTTP://example.com"
    assert f("mailto:jobs@example.com") == "mailto:jobs@example.com"
    for bad in (
        "javascript:alert(1)", "JaVaScRiPt:alert(1)", "  javascript:alert(1)", "java\tscript:alert(1)",
        "\x01javascript:alert(1)", "java\nscript:alert(1)", "data:text/html,x", "vbscript:x",
        "//evil.example", "", None,
    ):
        assert f(bad) == "#", bad


def test_detail_page_neutralizes_javascript_href(tmp_db):
    _insert(tmp_db)
    tmp_db.execute("UPDATE vacancies SET application_url = 'javascript:alert(1)' WHERE uuid='v1'")
    tmp_db.commit()
    request = Request({"type": "http", "method": "GET", "path": "/vacancy/v1", "headers": [], "query_string": b""})
    body = web_app.vacancy_detail(request, "v1").body.decode()
    assert "javascript:" not in body


def test_score_profile_next_open_redirect_blocked(tmp_db):
    for evil in ("https://evil.example", "//evil.example", "/\\evil.example", "evil.example"):
        resp = web_app.set_score_profile(profile="it", next=evil)
        assert resp.headers["location"] == "/", evil
    assert web_app.set_score_profile(profile="it", next="/kanban?x=1").headers["location"] == "/kanban?x=1"


def test_invalid_user_status_returns_400(tmp_db):
    _insert(tmp_db)
    request = Request({"type": "http", "method": "POST", "path": "/vacancy/v1/status", "headers": [], "query_string": b""})
    with pytest.raises(web_app.HTTPException) as exc:
        web_app.update_status(request, "v1", user_status="bogus")
    assert exc.value.status_code == 400


def test_update_status_uses_twin_aware_setter(tmp_db, monkeypatch):
    _insert(tmp_db)
    calls = []
    monkeypatch.setattr(
        web_app.scoring, "set_user_status_with_twins",
        lambda conn, uuid, status: calls.append((uuid, status)),
    )
    request = Request({"type": "http", "method": "POST", "path": "/vacancy/v1/status", "headers": [], "query_string": b""})
    web_app.update_status(request, "v1", user_status="applied")
    assert calls == [("v1", "applied")]


def test_sync_status_tolerates_legacy_summary_without_watermark(tmp_db):
    db.set_state(tmp_db, web_app.SYNC_STATE_KEY, json.dumps({"at": "01.01.2026 10:00"}))
    assert web_app.sync_status() == {"watermark_utc": None}


def _stub_sync_pipeline(monkeypatch, nav=None, jobbnorge=None):
    def boom(conn):
        raise RuntimeError("NAV token expired")

    monkeypatch.setattr(web_app.db, "backup_db", lambda: None)
    monkeypatch.setattr(web_app.nav_client, "sync", nav or (lambda conn: {"new": 1}))
    monkeypatch.setattr(web_app.jobbnorge_client, "sync", jobbnorge or (lambda conn: {"fetched": 2}))
    for mod in (web_app.finn_client, web_app.easycruit_client, web_app.linkedin_client):
        monkeypatch.setattr(mod, "sync", lambda conn: {})
    monkeypatch.setattr(web_app.scoring, "rescore_all", lambda conn: 0)


def test_sync_survives_nav_and_jobbnorge_failure_and_records_error(tmp_db, monkeypatch):
    def boom(conn):
        raise RuntimeError("NAV token expired")

    _stub_sync_pipeline(monkeypatch, nav=boom, jobbnorge=boom)
    summary = web_app.trigger_sync()
    assert summary["stats"] == {"error": "NAV token expired"}
    assert summary["jobbnorge"] == {"error": "NAV token expired"}
    stored = json.loads(db.get_state(tmp_db, web_app.SYNC_STATE_KEY))
    assert stored["stats"]["error"] == "NAV token expired"
    # The /sync route itself still redirects instead of 500ing.
    assert web_app.sync_route().status_code == 303


def test_index_renders_nav_and_jobbnorge_error_banners(tmp_db):
    summary = {
        "at": "05.10.2026 10:00", "watermark_utc": "2026-10-05 08:00:00",
        "stats": {"error": "NAV 401"}, "jobbnorge": {"error": "timeout"},
        "finn": {}, "easycruit": {}, "linkedin": {},
    }
    db.set_state(tmp_db, web_app.SYNC_STATE_KEY, json.dumps(summary))
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b""})
    body = web_app.index(request, user_status=[]).body.decode()
    assert "NAV не синхронізувався" in body
    assert "Jobbnorge не синхронізувався" in body


def test_sync_form_js_checks_response_ok():
    from pathlib import Path
    tpl = (Path(web_app.__file__).parent / "templates" / "index.html").read_text(encoding="utf-8")
    assert "resp.ok" in tpl and "alert(" in tpl


def test_filter_form_keeps_show_excluded_and_show_flagged_toggles(tmp_db):
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b""})
    plain = web_app.index(request, user_status=[]).body.decode()
    assert 'name="show_excluded"' not in plain and 'name="show_flagged"' not in plain
    both = web_app.index(request, user_status=[], show_excluded="1", show_flagged="1").body.decode()
    assert '<input type="hidden" name="show_excluded" value="1">' in both
    assert '<input type="hidden" name="show_flagged" value="1">' in both


def test_description_links_get_noopener_noreferrer():
    from web.render import sanitize_description
    out = sanitize_description('<a href="https://x.no">x</a>')
    assert 'rel="nofollow noopener noreferrer"' in out and 'target="_blank"' in out


def _render_index_with_summary(tmp_db, **extra):
    summary = {
        "at": "05.10.2026 10:00", "watermark_utc": "2026-10-05 08:00:00",
        "stats": {"new": 0, "updated": 0, "unchanged": 5, "marked_inactive": 0},
        "jobbnorge": {"fetched": 1}, "finn": {}, "easycruit": {}, "linkedin": {},
    }
    summary.update(extra)
    db.set_state(tmp_db, web_app.SYNC_STATE_KEY, json.dumps(summary))
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b""})
    return web_app.index(request, user_status=[]).body.decode()


def test_index_shows_nav_detail_errors_as_paused_import(tmp_db):
    body = _render_index_with_summary(
        tmp_db, stats={"new": 0, "updated": 0, "marked_inactive": 0, "detail_errors": 3},
    )
    assert "3 оголошень не завантажились" in body
    assert "імпорт призупинено" in body
    assert "імпорт призупинено" not in _render_index_with_summary(tmp_db)


def test_index_shows_finn_and_linkedin_parse_warnings(tmp_db):
    body = _render_index_with_summary(
        tmp_db,
        finn={"warning": "0 entries parsed from 4 messages"},
        linkedin={"warning": "LinkedIn digest format changed?"},
    )
    assert "finn.no: 0 entries parsed from 4 messages" in body
    assert "LinkedIn: LinkedIn digest format changed?" in body
