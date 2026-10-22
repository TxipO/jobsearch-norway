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
    status, _, out_body = _asgi_full(method, path, headers, body)
    return status, out_body


def _asgi_full(method, path, headers=None, body=b"", query=""):
    """ASGI round trip -> (status, response_headers_dict_lowercased, body_bytes)."""
    hdrs = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    if body:
        hdrs.append((b"content-type", b"application/x-www-form-urlencoded"))
        hdrs.append((b"content-length", str(len(body)).encode()))
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
        "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": query.encode(),
        "headers": hdrs, "server": ("127.0.0.1", 8000), "client": ("127.0.0.1", 50000),
    }
    out = {"status": None, "headers": {}, "body": b""}

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(msg):
        if msg["type"] == "http.response.start":
            out["status"] = msg["status"]
            out["headers"] = {k.decode().lower(): v.decode() for k, v in msg.get("headers", [])}
        elif msg["type"] == "http.response.body":
            out["body"] += msg.get("body", b"")

    asyncio.run(web_app.app(scope, receive, send))
    return out["status"], out["headers"], out["body"]


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
    # The exception type is part of the message (str(KeyError('uuid')) is just "'uuid'").
    assert summary["stats"] == {"error": "RuntimeError: NAV token expired"}
    assert summary["jobbnorge"] == {"error": "RuntimeError: NAV token expired"}
    stored = json.loads(db.get_state(tmp_db, web_app.SYNC_STATE_KEY))
    assert stored["stats"]["error"] == "RuntimeError: NAV token expired"
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
    assert "NAV:</strong> не синхронізувався" in body
    assert "Jobbnorge:</strong> не синхронізувався" in body


def test_concurrent_sync_is_rejected_then_next_runs(tmp_db, monkeypatch):
    # 2026-10-20: a second concurrent sync made jobbnorge fail with "database is locked".
    import threading

    started, release = threading.Event(), threading.Event()

    def blocking_nav(conn):
        started.set()
        release.wait(10)
        return {"new": 1}

    _stub_sync_pipeline(monkeypatch, nav=blocking_nav)
    first = threading.Thread(target=web_app.trigger_sync)
    first.start()
    assert started.wait(10)

    backups, sources = [], []
    monkeypatch.setattr(web_app.db, "backup_db", lambda: backups.append(1))
    monkeypatch.setattr(web_app.nav_client, "sync", lambda conn: sources.append(1) or {})
    with pytest.raises(web_app.SyncAlreadyRunning):
        web_app.trigger_sync()
    resp = web_app.sync_route()
    assert resp.status_code == 409
    assert backups == [] and sources == []

    release.set()
    first.join(10)
    assert not first.is_alive()
    # Lock released: a new sync runs normally.
    assert web_app.sync_route().status_code == 303
    assert backups == [1] and sources == [1]


def test_sync_lock_released_when_sync_raises(tmp_db, monkeypatch):
    _stub_sync_pipeline(monkeypatch)

    def boom():
        raise RuntimeError("no connection")

    # A nested context, NOT monkeypatch.undo(): undo() also reverted tmp_db's
    # db.connect patch, so the second sync below ran its delete/retire stages
    # and wrote its summary into the real data/jobsearch.db on every test run
    # (found 2026-10-22).
    with monkeypatch.context() as m:
        m.setattr(web_app, "get_conn", boom)
        with pytest.raises(RuntimeError):
            web_app.trigger_sync()
    assert web_app.trigger_sync()["scored"] == 0


def test_failing_source_is_logged_with_traceback_and_named(tmp_db, monkeypatch, caplog):
    """web/app.py had no logger: a swallowed source failure left no trace."""
    def boom(conn):
        raise KeyError("uuid")

    _stub_sync_pipeline(monkeypatch, nav=boom)
    with caplog.at_level("ERROR", logger="web.app"):
        summary = web_app.trigger_sync()
    assert summary["stats"] == {"error": "KeyError: 'uuid'"}
    rec = next(r for r in caplog.records if r.getMessage().startswith("Sync source "))
    assert rec.exc_info and rec.exc_info[0] is KeyError


def test_one_failing_stage_does_not_stop_later_stages_or_the_summary(tmp_db, monkeypatch, caplog):
    """Before 2026-10-22 an exception in rescore/delete_*/retire/auto-ignore
    propagated: every later stage and the summary write were skipped and the
    whole sync looked like it never happened."""
    _stub_sync_pipeline(monkeypatch)

    def boom(conn):
        raise RuntimeError("rescore blew up")

    calls = []
    monkeypatch.setattr(web_app.scoring, "rescore_all", boom)
    monkeypatch.setattr(web_app.db, "retire_stale_digest_rows", lambda conn: calls.append("retire") or 4)
    with caplog.at_level("ERROR", logger="web.app"):
        summary = web_app.trigger_sync()

    assert summary["stage_errors"] == [{"stage": "rescore", "error": "RuntimeError: rescore blew up"}]
    assert summary["scored"] == {}
    assert calls == ["retire"] and summary["retired_digest"] == 4
    stored = json.loads(db.get_state(tmp_db, web_app.SYNC_STATE_KEY))
    assert stored["stage_errors"] == summary["stage_errors"]
    assert any(r.exc_info for r in caplog.records)


def test_failing_summary_write_still_returns_the_summary(tmp_db, monkeypatch):
    _stub_sync_pipeline(monkeypatch)
    real = web_app.db.set_state

    def flaky(conn, key, value):
        if key == web_app.SYNC_STATE_KEY:
            raise RuntimeError("disk full")
        return real(conn, key, value)

    monkeypatch.setattr(web_app.db, "set_state", flaky)
    summary = web_app.trigger_sync()
    assert summary["stage_errors"] == [{"stage": "summary_write", "error": "RuntimeError: disk full"}]


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
    assert "finn.no:</strong> 0 entries parsed from 4 messages" in body
    assert "LinkedIn:</strong> LinkedIn digest format changed?" in body


def test_index_says_all_sources_are_fine_only_when_they_are(tmp_db):
    """Silence must be distinguishable from "checked, fine": the ✓ chip shows
    only when health.py found nothing, and never next to a problem."""
    from datetime import datetime, timezone
    fresh = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    ok = _render_index_with_summary(tmp_db, watermark_utc=fresh)
    assert "усі джерела в нормі" in ok
    bad = _render_index_with_summary(tmp_db, watermark_utc=fresh, linkedin={"error": "IMAP login failed"})
    assert "усі джерела в нормі" not in bad
    assert "LinkedIn:</strong> не синхронізувався цього разу — IMAP login failed" in bad


def test_index_warns_when_the_server_runs_older_code_than_the_files_on_disk(tmp_db, monkeypatch):
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b""})
    monkeypatch.setattr(web_app, "SERVER_STARTED_AT", 0.0)  # started "before" every file
    assert "Сервер працює зі старою версією коду" in web_app.index(request, user_status=[]).body.decode()
    monkeypatch.setattr(web_app, "SERVER_STARTED_AT", 4_000_000_000.0)  # started after every edit
    assert "Сервер працює зі старою версією коду" not in web_app.index(request, user_status=[]).body.decode()


def test_backup_sqlite_error_does_not_block_the_sync(tmp_db, monkeypatch):
    import sqlite3 as _sqlite3
    _stub_sync_pipeline(monkeypatch)

    def bad_backup():
        raise _sqlite3.DatabaseError("file is not a database")

    monkeypatch.setattr(web_app.db, "backup_db", bad_backup)
    summary = web_app.trigger_sync()
    assert summary["backup_failed"] == "DatabaseError: file is not a database"
    assert summary["stage_errors"] == []


def _assert_frame_blocked(headers):
    assert headers.get("x-frame-options") == "DENY"
    assert headers.get("content-security-policy") == "frame-ancestors 'none'"


def test_every_response_forbids_framing(tmp_db, monkeypatch):
    """Clickjacking: a page framing the local app would get Origin-guard-passing
    clicks. Headers must ride on normal, 404, guard 400/403 and 409 responses."""
    status, headers, _ = _asgi_full("GET", "/", HOST)
    assert status == 200
    _assert_frame_blocked(headers)

    status, headers, _ = _asgi_full("GET", "/no-such-page", HOST)
    assert status == 404
    _assert_frame_blocked(headers)

    status, headers, _ = _asgi_full("GET", "/", {"Host": "attacker.example:8000"})
    assert status == 400
    _assert_frame_blocked(headers)

    status, headers, _ = _asgi_full("POST", "/sync", {**HOST, "Origin": "https://evil.example"})
    assert status == 403
    _assert_frame_blocked(headers)

    assert web_app._sync_lock.acquire(blocking=False)
    try:
        status, headers, _ = _asgi_full("POST", "/sync", {**HOST, "Origin": "http://127.0.0.1:8000"})
    finally:
        web_app._sync_lock.release()
    assert status == 409
    _assert_frame_blocked(headers)


@pytest.mark.parametrize("name", ["min_score", "min_salary", "min_extent_percent", "page"])
@pytest.mark.parametrize("value", [
    "%C2%B2",                                    # "²": isdigit() True, int() ValueError -> was 500
    "%D9%A1%D9%A2%D9%A3",                        # Arabic-Indic digits
    "99999999999999999999",                      # overflows SQLite's 64-bit bind
    "-5", "", "abc", "1.5", "+3",
])
def test_index_numeric_filters_never_500(tmp_db, name, value):
    _insert(tmp_db)
    status, _, _ = _asgi_full("GET", "/", HOST, query=f"{name}={value}")
    assert status == 200


def test_parse_int_param():
    assert web_app._parse_int_param("42") == 42
    assert web_app._parse_int_param(" 7 ") == 7
    for bad in ("²", "١٢", "-1", "", "1" * 10, "1e3"):
        assert web_app._parse_int_param(bad) is None, bad


def test_error_text_masks_project_root_and_home_dir():
    """OSError messages embed the full path; the stored sync summary is shown
    in the UI, so the Windows username must not be in it."""
    from pathlib import Path

    root = Path(web_app.__file__).parent.parent
    for p in (root / "credentials" / "x.json", Path.home() / "elsewhere" / "y.txt"):
        for e in (FileNotFoundError(2, "No such file or directory", str(p)), RuntimeError(f"bad {p}")):
            text = web_app._error_text(e)
            assert str(Path.home()) not in text and str(root) not in text, text
            assert str(Path.home()).replace("\\", "\\\\") not in text, text
            assert type(e).__name__ in text


# --- /vacancy/{uuid}/document/{filename}: nothing outside GENERATED_DIR is ever served ---

SECRET = b"TOP-SECRET-OUTSIDE-GENERATED-DIR"


@pytest.fixture
def generated(tmp_path, monkeypatch):
    gen = (tmp_path / "generated").resolve()
    (gen / "v1").mkdir(parents=True)
    (gen / "v1" / "cv.pdf").write_bytes(b"%PDF-mine")
    # Decoys outside GENERATED_DIR that traversal would reach: tmp/cv.pdf, tmp/secret.txt
    (tmp_path / "cv.pdf").write_bytes(SECRET)
    (tmp_path / "soknad.pdf").write_bytes(SECRET)
    (tmp_path / "secret.txt").write_bytes(SECRET)
    monkeypatch.setattr(web_app, "GENERATED_DIR", gen)
    return gen


def test_document_happy_path(tmp_db, generated):
    status, _, body = _asgi_full("GET", "/vacancy/v1/document/cv.pdf", HOST)
    assert (status, body) == (200, b"%PDF-mine")


BS = chr(92)  # backslash, spelled out so no tool/shell layer can mangle escapes
NUL = chr(0)


@pytest.mark.parametrize("uuid", [
    "..", f"..{BS}", f"..{BS}..", "../..", "%2e%2e", "..%2f", "..%5c", "....//", ".", "",
    "C:", f"C:{BS}Windows", "/etc", f"{BS}{BS}host{BS}share", "v1/../..", f"v1{BS}..{BS}..", f"v1{NUL}", "~",
])
@pytest.mark.parametrize("filename", ["cv.pdf", "soknad.pdf"])
def test_document_uuid_traversal_never_serves_outside_files(tmp_db, generated, uuid, filename):
    status, _, body = _asgi_full("GET", f"/vacancy/{uuid}/document/{filename}", HOST)
    assert status in (400, 404)
    assert SECRET not in body


@pytest.mark.parametrize("filename", [
    "secret.txt", "../secret.txt", f"..{BS}secret.txt", "..%2fsecret.txt", "%2e%2e%2fsecret.txt",
    "/secret.txt", f"C:{BS}secret.txt", "cv.pdf.bak", "CV.PDF", "cv.pdf ", f"cv.pdf{NUL}.txt", "cv.docx", "..", "",
])
def test_document_filename_outside_allowlist_is_404(tmp_db, generated, filename):
    status, _, body = _asgi_full("GET", f"/vacancy/v1/document/{filename}", HOST)
    assert status in (400, 404)
    assert SECRET not in body
    assert web_app.GENERATED_DOC_NAMES == ("cv.pdf", "soknad.pdf")


def test_document_symlink_escape_is_blocked(tmp_db, generated, tmp_path):
    link = generated / "linked"
    try:
        link.symlink_to(tmp_path, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted here")
    status, _, body = _asgi_full("GET", "/vacancy/linked/document/cv.pdf", HOST)
    assert status == 404 and SECRET not in body


# --- POST /sync single-flight, through the middleware stack ---

def test_concurrent_sync_returns_409_through_middleware(tmp_db, monkeypatch):
    import threading

    started, release = threading.Event(), threading.Event()

    def blocking_nav(conn):
        started.set()
        release.wait(10)
        return {"new": 1}

    _stub_sync_pipeline(monkeypatch, nav=blocking_nav)
    first = threading.Thread(target=web_app.trigger_sync)
    first.start()
    try:
        assert started.wait(10)
        sources = []
        monkeypatch.setattr(web_app.jobbnorge_client, "sync", lambda conn: sources.append(1) or {})
        headers = {**HOST, "Origin": "http://127.0.0.1:8000"}
        status, resp_headers, body = _asgi_full("POST", "/sync", headers)
        assert status == 409
        assert json.loads(body) == {"error": "sync already running"}
        assert resp_headers["x-frame-options"] == "DENY"
        assert sources == []
        # A cross-origin POST is stopped by the guard, not mistaken for a running-sync 409.
        status, _, _ = _asgi_full("POST", "/sync", {**HOST, "Origin": "https://evil.example"})
        assert status == 403
    finally:
        release.set()
        first.join(10)
    assert not first.is_alive()
    status, resp_headers, _ = _asgi_full("POST", "/sync", {**HOST, "Origin": "http://127.0.0.1:8000"})
    assert status == 303 and resp_headers["location"] == "/"


def test_fonts_are_self_hosted():
    """No page may pull fonts (or anything) from Google: it leaks that the app is
    open and breaks offline use. Every font file the stylesheet references must
    exist and be served from /static/ with the guard + frame headers intact."""
    import re
    from pathlib import Path

    web_dir = Path(web_app.__file__).parent
    for tpl in (web_dir / "templates").glob("*.html"):
        text = tpl.read_text(encoding="utf-8")
        assert "googleapis" not in text and "gstatic" not in text, tpl.name
    css = (web_dir / "static" / "style.css").read_text(encoding="utf-8")
    assert "googleapis" not in css and "gstatic" not in css
    urls = re.findall(r"url\(\s*['\"]?(fonts/[^'\")\s]+)", css)
    assert len(urls) >= 6 and all(u.endswith(".woff2") for u in urls)
    for rel in urls:
        assert (web_dir / "static" / rel).stat().st_size > 1000, rel
    # OFL license text ships with each family.
    assert (web_dir / "static" / "fonts" / "Inter-OFL.txt").exists()
    assert (web_dir / "static" / "fonts" / "JetBrainsMono-OFL.txt").exists()

    status, headers, body = _asgi_full("GET", "/static/" + urls[0], HOST)
    assert status == 200 and body[:4] == b"wOF2"
    _assert_frame_blocked(headers)
    status, _, _ = _asgi_full("GET", "/static/" + urls[0], {"Host": "attacker.example:8000"})
    assert status == 400
