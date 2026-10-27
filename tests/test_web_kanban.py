"""Kanban shows "ignored" (user-requested 2026-10-27) but not "archived"."""

import db
from starlette.requests import Request

from tests.test_web_index_filters import _insert, _setup
from web import app as web_app


def test_kanban_shows_ignored_but_not_archived(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    _insert(conn, "job-ignored", title="Ignored job")
    db.set_user_status(conn, "job-ignored", "ignored")
    _insert(conn, "job-archived", title="Archived job")
    db.set_user_status(conn, "job-archived", "archived")

    request = Request({"type": "http", "method": "GET", "path": "/kanban", "headers": [], "query_string": b""})
    body = web_app.kanban(request).body.decode()
    assert "Ignored job" in body
    assert "Archived job" not in body
