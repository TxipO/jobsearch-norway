"""Regression tests for scoring.set_user_status_with_twins and the fill-only
rescore-time status propagation (2026-10-05, /fullreview deep)."""

import pytest

import db
from scoring import rescore_all, set_user_status_with_twins


def _make_conn(tmp_path):
    return db.connect(tmp_path / "test.db")


def _insert(conn, uuid, source, title="Driftsleder", business_name="Politiet", municipal="Oslo", status="ACTIVE"):
    db.upsert_vacancy_row(
        conn,
        {"uuid": uuid, "status": status, "title": title, "business_name": business_name,
         "municipal": municipal, "description": "A description long enough to not look like a summary line."},
        source=source,
    )


def _status(conn, uuid):
    return db.get_vacancy(conn, uuid)["user_status"]


def test_user_action_overwrites_twins_and_reset_to_new_sticks(tmp_path):
    conn = _make_conn(tmp_path)
    _insert(conn, "nav-1", "nav")
    _insert(conn, "finn-1", "finn")
    db.set_user_status(conn, "finn-1", "interesting")

    assert set_user_status_with_twins(conn, "nav-1", "applied") == 2
    assert (_status(conn, "nav-1"), _status(conn, "finn-1")) == ("applied", "applied")
    assert db.get_vacancy(conn, "finn-1")["applied_at"] is not None

    # Reset to 'new' must stick through a rescore (the old propagation
    # re-infected the reset row from its twin).
    assert set_user_status_with_twins(conn, "nav-1", "new") == 2
    rescore_all(conn)
    assert (_status(conn, "nav-1"), _status(conn, "finn-1")) == ("new", "new")


def test_user_action_leaves_same_source_and_unrelated_rows_alone(tmp_path):
    conn = _make_conn(tmp_path)
    _insert(conn, "finn-1", "finn", title="Lagermedarbeider", business_name="Coop AS", municipal="BERGEN")
    _insert(conn, "finn-2", "finn", title="Lagermedarbeider", business_name="Coop AS", municipal="BERGEN")
    _insert(conn, "nav-1", "nav", title="Lagermedarbeider", business_name="Coop AS", municipal="BERGEN")
    _insert(conn, "nav-2", "nav", title="Annet", business_name="Coop AS", municipal="BERGEN")
    _insert(conn, "li-1", "linkedin", title="Lagermedarbeider", business_name="Coop AS", municipal="BERGEN",
            status="INACTIVE")

    set_user_status_with_twins(conn, "finn-1", "archived")

    assert _status(conn, "finn-1") == "archived"
    assert _status(conn, "nav-1") == "archived"      # cross-source twin
    assert _status(conn, "finn-2") == "new"          # same source: distinct posting
    assert _status(conn, "nav-2") == "new"           # different key
    assert _status(conn, "li-1") == "new"            # INACTIVE twin untouched


def test_user_action_validates_status(tmp_path):
    conn = _make_conn(tmp_path)
    _insert(conn, "nav-1", "nav")
    with pytest.raises(ValueError):
        set_user_status_with_twins(conn, "nav-1", "bogus")
    assert set_user_status_with_twins(conn, "missing", "applied") == 0


def test_rescore_fills_new_twin_with_most_advanced_status_deterministically(tmp_path):
    conn = _make_conn(tmp_path)
    _insert(conn, "nav-1", "nav")
    _insert(conn, "jobbnorge-1", "jobbnorge")
    _insert(conn, "finn-1", "finn")
    db.set_user_status(conn, "nav-1", "interesting")
    db.set_user_status(conn, "jobbnorge-1", "interview")

    rescore_all(conn)

    assert _status(conn, "finn-1") == "interview"       # most advanced donor wins
    assert _status(conn, "nav-1") == "interesting"      # disagreeing twins untouched
    assert _status(conn, "jobbnorge-1") == "interview"
