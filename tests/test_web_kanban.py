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


def _kanban_body():
    request = Request({"type": "http", "method": "GET", "path": "/kanban", "headers": [], "query_string": b""})
    return web_app.kanban(request).body.decode()


def _row(conn, uuid, user_status, due=None, close=False):
    _insert(conn, uuid, title=f"Title {uuid}")
    conn.execute("UPDATE vacancies SET application_due_sort = ? WHERE uuid = ?", (due, uuid))
    conn.commit()
    db.set_user_status(conn, uuid, user_status)
    if close:
        conn.execute("UPDATE vacancies SET status = 'INACTIVE' WHERE uuid = ?", (uuid,))
        conn.commit()


def test_kanban_interesting_hides_closed_and_past_deadline(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    _row(conn, "int-closed", "interesting", close=True)
    _row(conn, "int-past", "interesting", due="2020-01-01")
    _row(conn, "int-null", "interesting", due=None)
    _row(conn, "int-future", "interesting", due="2999-01-01")
    _row(conn, "applied-closed", "applied", due="2020-01-01", close=True)

    body = _kanban_body()
    assert "Title int-closed" not in body
    assert "Title int-past" not in body
    assert "Title int-null" in body
    assert "Title int-future" in body
    assert "Title applied-closed" in body
    assert "+ 2 закритих/прострочених приховано" in body


def test_kanban_hidden_line_absent_when_nothing_hidden(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    _row(conn, "int-ok", "interesting")
    assert "приховано" not in _kanban_body()
