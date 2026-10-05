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
    # finn-1/finn-2 are two postings of one source, so nav-1 can't be tied to
    # either of them: the group is ambiguous, nothing propagates.
    assert _status(conn, "nav-1") == "new"
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


def _ambiguous_group(conn):
    """nav-a + nav-b are two distinct NAV postings (one per store); finn-c is
    a finn.no copy of ONE of them — which one is unknowable."""
    for uuid, source in (("nav-a", "nav"), ("nav-b", "nav"), ("finn-c", "finn")):
        _insert(conn, uuid, source, title="Lagermedarbeider", business_name="Coop AS", municipal="BERGEN")


def test_ambiguous_group_user_action_does_not_reach_other_source(tmp_path):
    """2026-10-05 review: trashing nav-a archived finn-c, a later rescore
    filled nav-b from finn-c, delete_archived() deleted all three."""
    conn = _make_conn(tmp_path)
    _ambiguous_group(conn)

    assert set_user_status_with_twins(conn, "nav-a", "archived") == 1
    assert [_status(conn, u) for u in ("nav-a", "nav-b", "finn-c")] == ["archived", "new", "new"]
    # ... and from the other side too.
    assert set_user_status_with_twins(conn, "finn-c", "applied") == 1
    assert [_status(conn, u) for u in ("nav-a", "nav-b", "finn-c")] == ["archived", "new", "applied"]


def test_ambiguous_group_rescore_does_not_fill_status(tmp_path):
    conn = _make_conn(tmp_path)
    _ambiguous_group(conn)
    db.set_user_status(conn, "finn-c", "archived")

    rescore_all(conn)

    assert [_status(conn, u) for u in ("nav-a", "nav-b", "finn-c")] == ["new", "new", "archived"]
    assert db.delete_archived(conn) == 1


def test_unambiguous_group_ignores_unrelated_inactive_duplicate(tmp_path):
    """Only ACTIVE rows count towards ambiguity: a dead second NAV row must
    not disable propagation for the live nav/finn pair."""
    conn = _make_conn(tmp_path)
    _insert(conn, "nav-1", "nav")
    _insert(conn, "nav-old", "nav", status="INACTIVE")
    _insert(conn, "finn-1", "finn")
    assert set_user_status_with_twins(conn, "nav-1", "applied") == 2
    assert _status(conn, "finn-1") == "applied" and _status(conn, "nav-old") == "new"


def test_twin_lookup_matches_non_ascii_municipal_case_insensitively(tmp_path):
    conn = _make_conn(tmp_path)
    _insert(conn, "nav-1", "nav", municipal="Ålesund")
    _insert(conn, "finn-1", "finn", municipal=" ÅLESUND ")
    _insert(conn, "finn-2", "finn", title="Annet", municipal="Ålesund")
    assert set_user_status_with_twins(conn, "nav-1", "interesting") == 2
    assert _status(conn, "finn-1") == "interesting" and _status(conn, "finn-2") == "new"
