"""Regression tests for db.py — pagination/count consistency (the two must
never drift since they share _vacancy_filters), migrations being safely
re-runnable, and the source-agnostic upsert path Jobbnorge/finn.no use."""

import db


def _make_conn(tmp_path):
    return db.connect(tmp_path / "test.db")


def _insert_vacancy(conn, uuid, source="test", **overrides):
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
    db.upsert_vacancy_row(conn, row, source=source)


def test_backup_db_creates_timestamped_copy(tmp_path):
    """2026-07-29: added after a real incident (a broad, unscoped browser
    click test scrambled user_status for ~50 real vacancies with no backup
    to recover from). Timestamped filenames so two backups taken in the
    same second under test still get distinct names — real usage is at
    most once per sync, but the test itself calls this rapidly."""
    db_path = tmp_path / "test.db"
    db.connect(db_path).close()

    backup_dir = tmp_path / "backups"
    dest = db.backup_db(db_path=db_path, backup_dir=backup_dir)

    assert dest is not None
    assert dest.exists()
    assert dest.parent == backup_dir
    assert dest.read_bytes() == db_path.read_bytes()


def test_backup_db_returns_none_for_missing_source(tmp_path):
    dest = db.backup_db(db_path=tmp_path / "nonexistent.db", backup_dir=tmp_path / "backups")
    assert dest is None


def test_backup_db_prunes_old_backups_beyond_keep_limit(tmp_path):
    db_path = tmp_path / "test.db"
    db.connect(db_path).close()
    backup_dir = tmp_path / "backups"

    # Write more "backup" files directly (bypassing the timestamp-per-second
    # granularity of backup_db itself) so pruning has something to prune.
    backup_dir.mkdir(parents=True)
    for i in range(5):
        (backup_dir / f"jobsearch_2026010{i}_000000.db").write_bytes(b"x")

    db.backup_db(db_path=db_path, backup_dir=backup_dir, keep=3)

    remaining = sorted(backup_dir.glob("jobsearch_*.db"))
    assert len(remaining) == 3
    # The newest ones (including the just-created one) survive; oldest pruned.
    assert "jobsearch_20260100_000000.db" not in {p.name for p in remaining}


def test_migrations_are_idempotent(tmp_path):
    conn = _make_conn(tmp_path)
    # Reconnecting re-runs _apply_migrations against an already-migrated
    # schema — must not raise (ALTER TABLE ADD COLUMN on an existing column
    # would error if the migration guard were broken).
    conn2 = db.connect(tmp_path / "test.db")
    assert conn2 is not None


def test_count_and_list_agree_on_total(tmp_path):
    conn = _make_conn(tmp_path)
    for i in range(7):
        _insert_vacancy(conn, f"job-{i}")

    total = db.count_vacancies(conn)
    all_rows = db.list_vacancies(conn, limit=100, offset=0)
    assert total == 7
    assert len(all_rows) == 7


def test_pagination_offset_matches_count(tmp_path):
    conn = _make_conn(tmp_path)
    for i in range(12):
        _insert_vacancy(conn, f"job-{i:02d}")

    page1 = db.list_vacancies(conn, limit=5, offset=0)
    page2 = db.list_vacancies(conn, limit=5, offset=5)
    page3 = db.list_vacancies(conn, limit=5, offset=10)

    assert len(page1) == 5
    assert len(page2) == 5
    assert len(page3) == 2  # 12 total, last page only has the remainder

    seen_uuids = {r["uuid"] for r in page1 + page2 + page3}
    assert len(seen_uuids) == 12  # no duplicates, no gaps across pages


def test_excluded_hidden_by_default_but_counted(tmp_path):
    conn = _make_conn(tmp_path)
    _insert_vacancy(conn, "visible-1")
    _insert_vacancy(conn, "hidden-1")
    db.set_exclusion(conn, "hidden-1", True, "test reason")

    assert db.count_vacancies(conn, show_excluded=False) == 1
    assert db.count_vacancies(conn, show_excluded=True) == 2
    assert db.count_excluded(conn) == 1

    visible = db.list_vacancies(conn, show_excluded=False)
    assert all(not r["excluded"] for r in visible)


def test_source_filter(tmp_path):
    conn = _make_conn(tmp_path)
    _insert_vacancy(conn, "nav-1")
    db.upsert_vacancy_row(
        conn,
        {"uuid": "jn-1", "status": "ACTIVE", "title": "Jobbnorge job", "description": "x"},
        source="jobbnorge",
    )

    assert db.count_vacancies(conn, source="jobbnorge") == 1
    assert set(db.list_sources(conn)) == {"test", "jobbnorge"}


def test_user_status_filter_accepts_a_list(tmp_path):
    """Multi-select status filter (2026-08-04) — user_status can now be a
    list, ORed together via IN(), on top of the old single-string call
    shape (kanban still passes one string per column)."""
    conn = _make_conn(tmp_path)
    _insert_vacancy(conn, "job-interesting")
    db.set_user_status(conn, "job-interesting", "interesting")
    _insert_vacancy(conn, "job-applied")
    db.set_user_status(conn, "job-applied", "applied")
    _insert_vacancy(conn, "job-rejected")
    db.set_user_status(conn, "job-rejected", "rejected")

    assert db.count_vacancies(conn, user_status=["interesting", "applied"]) == 2
    matched = {r["uuid"] for r in db.list_vacancies(conn, user_status=["interesting", "applied"])}
    assert matched == {"job-interesting", "job-applied"}

    # Old shape (bare string) must still work — kanban's own call site.
    assert db.count_vacancies(conn, user_status="rejected") == 1


def test_inactive_status_hidden_from_active_only(tmp_path):
    conn = _make_conn(tmp_path)
    _insert_vacancy(conn, "active-1")
    _insert_vacancy(conn, "inactive-1", status="INACTIVE")

    assert db.count_vacancies(conn, active_only=True) == 1
    assert db.count_vacancies(conn, active_only=False) == 2


def test_active_only_exempts_reacted_statuses_beyond_new_and_interesting(tmp_path):
    """User-requested 2026-08-16: any vacancy the user has moved past the
    untouched-backlog states must stay visible everywhere — search, every
    explicit filter combination — even after the source closes the listing.
    'new' and 'interesting' are still backlog, so they stay subject to the
    ACTIVE-only rule; every other status is exempt from ACTIVE-only itself,
    independent of whether the *default unfiltered* list also hides it (see
    test_only_new_and_interesting_shown_by_default below — a separate
    rule)."""
    conn = _make_conn(tmp_path)
    for status in ("applied", "interview", "offer", "rejected", "ignored", "archived"):
        _insert_vacancy(conn, f"inactive-{status}", status="INACTIVE")
        db.set_user_status(conn, f"inactive-{status}", status)

    # None of these are "new"/"interesting", so all are absent from the
    # default (unfiltered) view regardless of ACTIVE-only.
    visible = {r["uuid"] for r in db.list_vacancies(conn, active_only=True)}
    for status in ("applied", "interview", "offer", "rejected", "ignored", "archived"):
        assert f"inactive-{status}" not in visible
        filtered = {r["uuid"] for r in db.list_vacancies(conn, active_only=True, user_status=status)}
        assert f"inactive-{status}" in filtered, f"{status} must still be exempt from ACTIVE-only when filtered"


def test_only_new_and_interesting_shown_by_default(tmp_path):
    """User-requested 2026-08-23 (rejected/ignored only) then widened
    2026-09-02 to "чисто Нове та Цікаво": every reacted-to status clutters
    the default main list once there are enough of them — hide all of them
    by default, but keep each one filter-panel click away rather than making
    them disappear outright."""
    conn = _make_conn(tmp_path)
    _insert_vacancy(conn, "v-new")
    db.set_user_status(conn, "v-new", "new")
    _insert_vacancy(conn, "v-interesting")
    db.set_user_status(conn, "v-interesting", "interesting")
    hidden_statuses = ("applied", "interview", "offer", "rejected", "ignored")
    for status in hidden_statuses:
        _insert_vacancy(conn, f"v-{status}")
        db.set_user_status(conn, f"v-{status}", status)

    default_view = {r["uuid"] for r in db.list_vacancies(conn)}
    assert default_view == {"v-new", "v-interesting"}

    assert db.count_vacancies(conn) == 2
    assert db.count_vacancies(conn, user_status=list(hidden_statuses)) == len(hidden_statuses)

    for status in hidden_statuses:
        filtered = {r["uuid"] for r in db.list_vacancies(conn, user_status=status)}
        assert filtered == {f"v-{status}"}


def test_search_ignores_the_default_status_restriction_except_archived(tmp_path):
    """User-requested 2026-09-03: the plain-listing default above (only
    "new"/"interesting") must NOT apply to a search — a search is a
    deliberate lookup, and the user could not find a vacancy they had
    already applied to by searching its name. Search looks across every
    status except "archived" ("Смітник" — those rows are meant to be gone
    outright)."""
    conn = _make_conn(tmp_path)
    for status in ("new", "interesting", "applied", "interview", "offer", "rejected", "ignored"):
        _insert_vacancy(conn, f"v-{status}", title=f"Findable Corp {status}")
        db.set_user_status(conn, f"v-{status}", status)
    _insert_vacancy(conn, "v-archived", title="Findable Corp archived")
    db.set_user_status(conn, "v-archived", "archived")

    found = {r["uuid"] for r in db.list_vacancies(conn, search="Findable")}
    assert found == {
        "v-new", "v-interesting", "v-applied", "v-interview", "v-offer", "v-rejected", "v-ignored",
    }
    assert db.count_vacancies(conn, search="Findable") == 7

    # Explicit status filter still wins over the search-wide default.
    only_applied = {r["uuid"] for r in db.list_vacancies(conn, search="Findable", user_status="applied")}
    assert only_applied == {"v-applied"}

    # ...and archived is reachable by name only through its own explicit filter.
    only_archived = {r["uuid"] for r in db.list_vacancies(conn, search="Findable", user_status="archived")}
    assert only_archived == {"v-archived"}


def test_search_is_space_and_case_insensitive(tmp_path):
    """Live report 2026-09-03: searching "Supermicro" (one word) found
    nothing for a vacancy whose employer name is "Super Micro Computer" —
    exactly as the source wrote it, with spaces. Space/case differences on
    either side of the query must not matter."""
    conn = _make_conn(tmp_path)
    _insert_vacancy(conn, "v1", title="Service Engineer", business_name="Super Micro Computer")
    db.set_user_status(conn, "v1", "new")

    for query in ("Supermicro", "SUPERMICRO", "super  micro", "MicroComputer", "micro computer"):
        found = {r["uuid"] for r in db.list_vacancies(conn, search=query)}
        assert found == {"v1"}, f"query {query!r} should find v1"


def test_active_only_still_hides_interesting_and_new_when_inactive(tmp_path):
    conn = _make_conn(tmp_path)
    _insert_vacancy(conn, "inactive-interesting", status="INACTIVE")
    db.set_user_status(conn, "inactive-interesting", "interesting")
    _insert_vacancy(conn, "inactive-new", status="INACTIVE")

    visible = {r["uuid"] for r in db.list_vacancies(conn, active_only=True)}
    assert "inactive-interesting" not in visible
    assert "inactive-new" not in visible


def test_delete_inactive_removes_only_inactive(tmp_path):
    conn = _make_conn(tmp_path)
    _insert_vacancy(conn, "keep-1", status="ACTIVE")
    _insert_vacancy(conn, "gone-1", status="INACTIVE")

    deleted = db.delete_inactive(conn)
    assert deleted == 1
    remaining = {r["uuid"] for r in db.list_vacancies(conn, active_only=False)}
    assert remaining == {"keep-1"}


def test_delete_inactive_keeps_user_reacted_rows(tmp_path):
    """A vacancy the user applied to must survive NAV closing the listing —
    same principle as delete_expired_unreacted, applied to delete_inactive."""
    conn = _make_conn(tmp_path)
    _insert_vacancy(conn, "closed-but-applied", status="INACTIVE")
    db.set_user_status(conn, "closed-but-applied", "applied")
    _insert_vacancy(conn, "closed-untouched", status="INACTIVE")

    deleted = db.delete_inactive(conn)
    assert deleted == 1
    # Explicit user_status=all: this checks what's left in the table after
    # deletion, not the default (new/interesting-only) view's own filtering.
    remaining = {r["uuid"] for r in db.list_vacancies(conn, active_only=False, user_status=list(db.USER_STATUSES))}
    assert remaining == {"closed-but-applied"}


def test_set_flagged_hides_from_default_list_but_stays_countable(tmp_path):
    conn = _make_conn(tmp_path)
    _insert_vacancy(conn, "visible-1")
    _insert_vacancy(conn, "flagged-1")
    db.set_flagged(conn, "flagged-1", True)

    assert db.count_vacancies(conn, show_flagged=False) == 1
    assert db.count_vacancies(conn, show_flagged=True) == 2
    assert db.count_flagged(conn) == 1

    visible = db.list_vacancies(conn, show_flagged=False)
    assert {r["uuid"] for r in visible} == {"visible-1"}


def test_set_flagged_toggle_clears_it(tmp_path):
    conn = _make_conn(tmp_path)
    _insert_vacancy(conn, "job-1")
    db.set_flagged(conn, "job-1", True)
    assert db.get_vacancy(conn, "job-1")["flagged_at"] is not None

    db.set_flagged(conn, "job-1", False)
    assert db.get_vacancy(conn, "job-1")["flagged_at"] is None
    assert db.count_flagged(conn) == 0


def test_list_flagged_returns_oldest_first(tmp_path):
    """set_flagged() uses datetime('now') at second precision — two calls in
    the same test could tie, so this sets distinct timestamps directly
    rather than relying on real-clock ordering to not flake."""
    conn = _make_conn(tmp_path)
    _insert_vacancy(conn, "job-a")
    _insert_vacancy(conn, "job-b")
    conn.execute("UPDATE vacancies SET flagged_at = '2026-01-02 00:00:00' WHERE uuid = 'job-b'")
    conn.execute("UPDATE vacancies SET flagged_at = '2026-01-01 00:00:00' WHERE uuid = 'job-a'")
    conn.commit()

    flagged = db.list_flagged(conn)
    assert [r["uuid"] for r in flagged] == ["job-a", "job-b"]


def test_list_flagged_matches_count_flagged_active_scope(tmp_path):
    """code-review 2026-07-19: count_flagged() only counted ACTIVE rows but
    list_flagged() didn't check status at all — the badge and the review
    queue could silently disagree once a flagged vacancy went inactive."""
    conn = _make_conn(tmp_path)
    _insert_vacancy(conn, "active-flagged", status="ACTIVE")
    _insert_vacancy(conn, "inactive-flagged", status="INACTIVE")
    db.set_flagged(conn, "active-flagged", True)
    db.set_flagged(conn, "inactive-flagged", True)

    assert db.count_flagged(conn) == 1
    assert {r["uuid"] for r in db.list_flagged(conn)} == {"active-flagged"}


def test_flagged_vacancy_still_visible_with_show_flagged_and_user_status(tmp_path):
    """code-review 2026-07-19: kanban() calls list_vacancies with
    active_only=False + user_status=<column> + show_flagged=True — a
    vacancy already tracked as 'interesting' must not vanish just because
    it's also flagged as buggy (flagging and application status are
    independent axes)."""
    conn = _make_conn(tmp_path)
    _insert_vacancy(conn, "job-1")
    db.set_user_status(conn, "job-1", "interesting")
    db.set_flagged(conn, "job-1", True)

    visible = db.list_vacancies(conn, active_only=False, user_status="interesting", show_flagged=True)
    assert {r["uuid"] for r in visible} == {"job-1"}

    hidden = db.list_vacancies(conn, active_only=False, user_status="interesting", show_flagged=False)
    assert hidden == []


def test_list_vacancies_includes_flagged_at_column(tmp_path):
    """Found via manual end-to-end verification, not code-review: list_vacancies
    has an explicit SELECT column list (not `SELECT *`) that omitted
    flagged_at — templates check `v.flagged_at` to render the 🚩 button's
    active state on page load (_flag_control.html, included from both
    index.html and kanban.html), so every card silently showed 'not
    flagged' regardless of true state until reloaded via a route that uses
    db.get_vacancy (SELECT *) instead, like the flag toggle's own HTMX
    response."""
    conn = _make_conn(tmp_path)
    _insert_vacancy(conn, "job-1")
    db.set_flagged(conn, "job-1", True)

    row = db.list_vacancies(conn, show_flagged=True)[0]
    assert row["flagged_at"] is not None


def test_set_notes_saves_and_clears(tmp_path):
    conn = _make_conn(tmp_path)
    _insert_vacancy(conn, "job-1")

    db.set_notes(conn, "job-1", "Sent CV via referral, waiting since June.")
    assert db.get_vacancy(conn, "job-1")["notes"] == "Sent CV via referral, waiting since June."

    # Empty string normalizes to NULL, same "no note" state as never having set one.
    db.set_notes(conn, "job-1", "")
    assert db.get_vacancy(conn, "job-1")["notes"] is None


def test_list_vacancies_includes_notes_column(tmp_path):
    """Same failure shape as the earlier flagged_at bug (see
    test_list_vacancies_includes_flagged_at_column) — list_vacancies has an
    explicit SELECT column list, easy to add a new field to db.py and
    forget to add it here too."""
    conn = _make_conn(tmp_path)
    _insert_vacancy(conn, "job-1")
    db.set_notes(conn, "job-1", "A note.")

    row = db.list_vacancies(conn)[0]
    assert row["notes"] == "A note."


def test_offer_is_a_valid_user_status(tmp_path):
    """2026-07-21, user-requested: "offer" (passed interview, received an
    offer) is a distinct outcome from "interview" (still pending)."""
    conn = _make_conn(tmp_path)
    _insert_vacancy(conn, "job-1")
    db.set_user_status(conn, "job-1", "offer")
    assert db.get_vacancy(conn, "job-1")["user_status"] == "offer"
    assert "offer" in db.USER_STATUSES


def test_delete_expired_unreacted_respects_user_status(tmp_path):
    conn = _make_conn(tmp_path)
    _insert_vacancy(conn, "expired-new", application_due="2020-01-01", status="ACTIVE")
    _insert_vacancy(conn, "expired-applied", application_due="2020-01-01", status="ACTIVE")
    db.set_user_status(conn, "expired-applied", "applied")
    _insert_vacancy(conn, "future-new", application_due="2099-01-01", status="ACTIVE")
    _insert_vacancy(conn, "free-text-deadline", application_due="Fortløpende opptak", status="ACTIVE")

    deleted = db.delete_expired_unreacted(conn)
    assert deleted == 1

    # Explicit user_status=all: checking what's left in the table, not the
    # default (new/interesting-only) view's own filtering.
    remaining = {r["uuid"] for r in db.list_vacancies(conn, active_only=False, user_status=list(db.USER_STATUSES))}
    assert remaining == {"expired-applied", "future-new", "free-text-deadline"}


def test_set_user_status_stamps_applied_at(tmp_path):
    conn = _make_conn(tmp_path)
    _insert_vacancy(conn, "v1")
    row = conn.execute("SELECT applied_at FROM vacancies WHERE uuid = 'v1'").fetchone()
    assert row["applied_at"] is None

    db.set_user_status(conn, "v1", "applied")
    row = conn.execute("SELECT applied_at, user_status FROM vacancies WHERE uuid = 'v1'").fetchone()
    assert row["user_status"] == "applied"
    assert row["applied_at"] is not None

    # A later, unrelated status change leaves the stamp untouched — it's
    # only meaningful while user_status is still "applied" anyway.
    db.set_user_status(conn, "v1", "interview")
    row2 = conn.execute("SELECT applied_at FROM vacancies WHERE uuid = 'v1'").fetchone()
    assert row2["applied_at"] == row["applied_at"]


def test_auto_ignore_stale_applications_after_two_months_no_deadline(tmp_path):
    conn = _make_conn(tmp_path)
    _insert_vacancy(conn, "stale", application_due=None)
    db.set_user_status(conn, "stale", "applied")
    conn.execute("UPDATE vacancies SET applied_at = datetime('now', '-3 months') WHERE uuid = 'stale'")
    conn.commit()

    ignored = db.auto_ignore_stale_applications(conn)
    assert ignored == 1
    assert conn.execute("SELECT user_status FROM vacancies WHERE uuid = 'stale'").fetchone()["user_status"] == "ignored"


def test_auto_ignore_stale_applications_not_yet_due(tmp_path):
    conn = _make_conn(tmp_path)
    _insert_vacancy(conn, "recent", application_due=None)
    db.set_user_status(conn, "recent", "applied")
    conn.execute("UPDATE vacancies SET applied_at = datetime('now', '-3 weeks') WHERE uuid = 'recent'")
    conn.commit()

    assert db.auto_ignore_stale_applications(conn) == 0
    assert conn.execute("SELECT user_status FROM vacancies WHERE uuid = 'recent'").fetchone()["user_status"] == "applied"


def test_auto_ignore_stale_applications_waits_for_future_deadline(tmp_path):
    """Applied 3 months ago (past the threshold on its own), but the
    listing's own deadline is still a month away — the silence clock
    shouldn't start until the deadline itself passes, since the employer
    plausibly hasn't started reviewing yet."""
    conn = _make_conn(tmp_path)
    _insert_vacancy(conn, "future-deadline")
    db.set_user_status(conn, "future-deadline", "applied")
    conn.execute(
        "UPDATE vacancies SET applied_at = datetime('now', '-3 months'), "
        "application_due_sort = date('now', '+1 month') WHERE uuid = 'future-deadline'"
    )
    conn.commit()

    assert db.auto_ignore_stale_applications(conn) == 0


def test_auto_ignore_stale_applications_ignores_other_statuses(tmp_path):
    conn = _make_conn(tmp_path)
    _insert_vacancy(conn, "interesting-old")
    db.set_user_status(conn, "interesting-old", "interesting")
    conn.execute("UPDATE vacancies SET applied_at = datetime('now', '-6 months') WHERE uuid = 'interesting-old'")
    conn.commit()

    assert db.auto_ignore_stale_applications(conn) == 0
    assert conn.execute("SELECT user_status FROM vacancies WHERE uuid = 'interesting-old'").fetchone()["user_status"] == "interesting"


def test_auto_ignore_stale_applications_skips_when_neither_date_known(tmp_path):
    """Rows that were already "applied" before the applied_at column
    existed AND have no known deadline either have no recorded date to
    count from at all — never guessed, never auto-ignored."""
    conn = _make_conn(tmp_path)
    _insert_vacancy(conn, "no-timestamp")
    db.set_user_status(conn, "no-timestamp", "applied")
    conn.execute("UPDATE vacancies SET applied_at = NULL WHERE uuid = 'no-timestamp'")
    conn.commit()

    assert db.auto_ignore_stale_applications(conn) == 0


def test_auto_ignore_stale_applications_backfills_from_deadline_alone(tmp_path):
    """User-requested 2026-08-30: a row that was already "applied" before
    applied_at existed (so that date is unknown) still auto-ignores off
    application_due_sort ALONE when a deadline is known — that's real
    recorded data, not a guess, unlike applied_at itself."""
    conn = _make_conn(tmp_path)
    _insert_vacancy(conn, "old-applied-with-deadline")
    db.set_user_status(conn, "old-applied-with-deadline", "applied")
    conn.execute(
        "UPDATE vacancies SET applied_at = NULL, application_due_sort = date('now', '-3 months') "
        "WHERE uuid = 'old-applied-with-deadline'"
    )
    conn.commit()

    assert db.auto_ignore_stale_applications(conn) == 1
    row = conn.execute("SELECT user_status FROM vacancies WHERE uuid = 'old-applied-with-deadline'").fetchone()
    assert row["user_status"] == "ignored"


def test_get_auto_ignore_date_backfills_from_deadline_alone(tmp_path):
    conn = _make_conn(tmp_path)
    assert db.get_auto_ignore_date(conn, None, "2026-06-01") == "2026-08-01"


def test_get_auto_ignore_date_prefers_later_deadline(tmp_path):
    conn = _make_conn(tmp_path)
    d = db.get_auto_ignore_date(conn, "2026-01-01 00:00:00", "2026-06-01")
    assert d == "2026-08-01"


def test_get_auto_ignore_date_falls_back_to_applied_at(tmp_path):
    conn = _make_conn(tmp_path)
    d = db.get_auto_ignore_date(conn, "2026-01-01 00:00:00", None)
    assert d == "2026-03-01"


def test_get_auto_ignore_date_none_when_neither_date_known(tmp_path):
    conn = _make_conn(tmp_path)
    assert db.get_auto_ignore_date(conn, None, None) is None


def test_delete_archived_ignores_source_status(tmp_path):
    """"archived" ("Смітник") is user-requested deletion, the opposite of
    every other reacted status — unlike delete_inactive/
    delete_expired_unreacted, it must remove the row regardless of whether
    the source still considers the listing ACTIVE."""
    conn = _make_conn(tmp_path)
    _insert_vacancy(conn, "trashed-active", status="ACTIVE")
    db.set_user_status(conn, "trashed-active", "archived")
    _insert_vacancy(conn, "trashed-inactive", status="INACTIVE")
    db.set_user_status(conn, "trashed-inactive", "archived")
    _insert_vacancy(conn, "kept-applied", status="ACTIVE")
    db.set_user_status(conn, "kept-applied", "applied")

    deleted = db.delete_archived(conn)
    assert deleted == 2

    # Explicit user_status=all: checking what's left in the table, not the
    # default (new/interesting-only) view's own filtering.
    remaining = {r["uuid"] for r in db.list_vacancies(conn, active_only=False, user_status=list(db.USER_STATUSES))}
    assert remaining == {"kept-applied"}


def test_upsert_is_idempotent_and_updates_fields(tmp_path):
    conn = _make_conn(tmp_path)
    _insert_vacancy(conn, "job-1", title="Original Title")
    _insert_vacancy(conn, "job-1", title="Updated Title")

    assert db.count_vacancies(conn) == 1
    row = db.get_vacancy(conn, "job-1")
    assert row["title"] == "Updated Title"


def test_upsert_preserves_description_when_new_value_is_none(tmp_path):
    """Live bug found 2026-07-18: finn.no's own sync always upserts
    description=None (finn_client.py never has a real one to give) — without
    the COALESCE fix in upsert_vacancy_row's ON CONFLICT clause, that would
    silently wipe out a description borrowed via
    scoring._build_description_lender_lookup on the very next sync. Same
    failure shape as the jobbnorge full-description-backfill bug fixed
    2026-07-17, just for a different field-population path."""
    conn = _make_conn(tmp_path)
    _insert_vacancy(conn, "finn-1", source="finn", description="Borrowed text from a matching NAV listing.")
    _insert_vacancy(conn, "finn-1", source="finn", description=None)
    assert db.get_vacancy(conn, "finn-1")["description"] == "Borrowed text from a matching NAV listing."


def test_set_borrowed_description(tmp_path):
    conn = _make_conn(tmp_path)
    _insert_vacancy(conn, "finn-1", source="finn", description=None)
    db.set_borrowed_description(
        conn, "finn-1",
        "Vi søker en engasjert medarbeider til vårt kontor i Bergen sentrum, med gode muligheter for faglig utvikling.",
        "nav-1",
    )
    row = db.get_vacancy(conn, "finn-1")
    assert row["description"].startswith("Vi søker en engasjert")
    assert row["description_borrowed_from"] == "nav-1"
    assert row["language"] is not None  # re-detected from the borrowed text


def test_set_borrowed_description_uses_explicit_language_when_given(tmp_path):
    """code-review 2026-07-19: rescore_all precomputes language once and
    passes it in, so set_borrowed_description must use that value instead
    of re-detecting (which would run langdetect on the same text twice)."""
    conn = _make_conn(tmp_path)
    _insert_vacancy(conn, "finn-1", source="finn", description=None)
    db.set_borrowed_description(
        conn, "finn-1", "Some real Norwegian text about a job posting here.", "nav-1", language="no",
    )
    assert db.get_vacancy(conn, "finn-1")["language"] == "no"


def test_set_borrowed_description_clears(tmp_path):
    """Must be able to clear a stale borrow (the lending row expired/got
    deleted since the last rescore), not just set one."""
    conn = _make_conn(tmp_path)
    _insert_vacancy(conn, "finn-1", source="finn", description=None)
    db.set_borrowed_description(conn, "finn-1", "Some borrowed text here for testing purposes.", "nav-1")
    db.set_borrowed_description(conn, "finn-1", None, None)
    row = db.get_vacancy(conn, "finn-1")
    assert row["description"] is None
    assert row["description_borrowed_from"] is None


def test_rows_needing_full_description_excludes_recent_attempts(tmp_path):
    """Regression test for a live bug (2026-07-17): jobbnorge_client's
    backfill retried the same permanently-404ing rows on every single
    sync — 35s wasted per click for zero benefit. A row whose fetch was
    attempted recently must not be offered again until the cooldown
    passes, but a brand-new row (never attempted) always is."""
    conn = _make_conn(tmp_path)
    _insert_vacancy(conn, "never-tried", description="short", source="jobbnorge")
    _insert_vacancy(conn, "tried-recently", description="short", source="jobbnorge")
    db.mark_description_fetch_attempted(conn, "tried-recently")
    conn.commit()

    needing = {r["uuid"] for r in db.rows_needing_full_description(conn, source="jobbnorge")}
    assert needing == {"never-tried"}


def test_min_score_filter(tmp_path):
    conn = _make_conn(tmp_path)
    _insert_vacancy(conn, "low")
    _insert_vacancy(conn, "high")
    db.set_score(conn, "low", 20, {})
    db.set_score(conn, "high", 80, {})

    assert db.count_vacancies(conn, min_score=50) == 1
    visible = db.list_vacancies(conn, min_score=50)
    assert {r["uuid"] for r in visible} == {"high"}
    # No filter at all still returns both, including the unscored default.
    assert db.count_vacancies(conn) == 2


def test_score_profile_selects_independent_column(tmp_path):
    """2026-08-26: dual scoring profiles ("warehouse" default, "it" toggle) —
    each vacancy carries two independently-scored, independently-filterable
    columns. count_vacancies/list_vacancies must read/filter/sort by whichever
    profile is requested, never mixing the two."""
    conn = _make_conn(tmp_path)
    _insert_vacancy(conn, "warehouse-fit")
    _insert_vacancy(conn, "it-fit")
    db.set_score(conn, "warehouse-fit", 80, {"a": {"points": 80, "matched": []}})
    db.set_score(conn, "it-fit", 10, {"a": {"points": 10, "matched": []}})
    db.set_score_it(conn, "warehouse-fit", 10, {"b": {"points": 10, "matched": []}})
    db.set_score_it(conn, "it-fit", 80, {"b": {"points": 80, "matched": []}})

    assert db.count_vacancies(conn, min_score=50, score_profile="warehouse") == 1
    assert db.count_vacancies(conn, min_score=50, score_profile="it") == 1
    visible_warehouse = db.list_vacancies(conn, min_score=50, score_profile="warehouse")
    visible_it = db.list_vacancies(conn, min_score=50, score_profile="it")
    assert {r["uuid"] for r in visible_warehouse} == {"warehouse-fit"}
    assert {r["uuid"] for r in visible_it} == {"it-fit"}
    # The aliased "score" column in the row must reflect the requested profile.
    assert visible_warehouse[0]["score"] == 80
    assert visible_it[0]["score"] == 80


def test_score_profile_rejects_unknown_value(tmp_path):
    conn = _make_conn(tmp_path)
    try:
        db.count_vacancies(conn, min_score=1, score_profile="bogus")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_min_salary_filter(tmp_path):
    conn = _make_conn(tmp_path)
    _insert_vacancy(conn, "no-salary")
    _insert_vacancy(conn, "low-salary")
    _insert_vacancy(conn, "high-salary")
    db.set_salary_text(conn, "low-salary", "kr 350 000", 350000)
    db.set_salary_text(conn, "high-salary", "kr 700 000", 700000)

    assert db.count_vacancies(conn, min_salary=500000) == 1
    visible = db.list_vacancies(conn, min_salary=500000)
    assert {r["uuid"] for r in visible} == {"high-salary"}
    # A row with no stated salary never matches a min_salary filter.
    assert "no-salary" not in {r["uuid"] for r in db.list_vacancies(conn, min_salary=0)}


def test_min_extent_percent_filter(tmp_path):
    """User-requested 2026-07-25, after confusing this with min_score
    (both are "% something" fields with no distinct label at the time) —
    min_extent_percent filters on employment share (full vs part time),
    a completely different axis from the match-quality score."""
    conn = _make_conn(tmp_path)
    _insert_vacancy(conn, "part-time")
    _insert_vacancy(conn, "full-time")
    _insert_vacancy(conn, "unknown-extent")
    db.set_extent_percent(conn, "part-time", 50)
    db.set_extent_percent(conn, "full-time", 100)

    assert db.count_vacancies(conn, min_extent_percent=80) == 1
    visible = db.list_vacancies(conn, min_extent_percent=80)
    assert {r["uuid"] for r in visible} == {"full-time"}
    # A row where extent_percent couldn't be parsed at all never matches.
    assert "unknown-extent" not in {r["uuid"] for r in db.list_vacancies(conn, min_extent_percent=0)}


def test_sort_by_deadline_pushes_free_text_to_the_end(tmp_path):
    conn = _make_conn(tmp_path)
    _insert_vacancy(conn, "far", application_due="2099-01-01")
    _insert_vacancy(conn, "soon", application_due="2026-08-01")
    _insert_vacancy(conn, "free-text", application_due="Løpende")
    _insert_vacancy(conn, "no-deadline", application_due=None)

    ordered = [r["uuid"] for r in db.list_vacancies(conn, sort="deadline")]
    assert ordered.index("soon") < ordered.index("far")
    assert ordered.index("far") < ordered.index("free-text")
    assert ordered.index("far") < ordered.index("no-deadline")


def test_sort_by_deadline_handles_non_iso_raw_shapes(tmp_path):
    """2026-08-27 live bug: NAV stores application_due verbatim (not always
    ISO — see normalize_due_date's docstring), so a real near-term deadline
    written as "30.08.2026" or "10-09-2026" was silently sorted as if it had
    no deadline at all (fell to the GLOB-miss branch, pushed to the very
    end) instead of interleaving correctly among ISO-dated rows."""
    conn = _make_conn(tmp_path)
    _insert_vacancy(conn, "iso-far", application_due="2026-09-27")
    _insert_vacancy(conn, "dot-soonest", application_due="30.08.2026")
    _insert_vacancy(conn, "dash-mid", application_due="10-09-2026")
    _insert_vacancy(conn, "free-text", application_due="Løpende")

    ordered = [r["uuid"] for r in db.list_vacancies(conn, sort="deadline")]
    assert ordered == ["dot-soonest", "dash-mid", "iso-far", "free-text"]


def test_normalize_due_date_all_known_shapes():
    assert db.normalize_due_date("2026-09-08T21:59:59") == "2026-09-08"
    assert db.normalize_due_date("30.08.2026") == "2026-08-30"
    assert db.normalize_due_date("31.8.2026") == "2026-08-31"
    assert db.normalize_due_date("10-09-2026") == "2026-09-10"
    assert db.normalize_due_date("Løpende") is None
    assert db.normalize_due_date(None) is None


def test_count_new_high_score_only_counts_after_watermark(tmp_path):
    conn = _make_conn(tmp_path)
    _insert_vacancy(conn, "old-high")
    db.set_score(conn, "old-high", 80, {})
    conn.execute("UPDATE vacancies SET first_seen_at = '2020-01-01 00:00:00' WHERE uuid = 'old-high'")
    _insert_vacancy(conn, "new-high")
    db.set_score(conn, "new-high", 80, {})
    _insert_vacancy(conn, "new-low")
    db.set_score(conn, "new-low", 20, {})
    conn.commit()

    assert db.count_new_high_score(conn, since="2025-01-01 00:00:00", min_score=55) == 1


def test_count_new_high_score_excludes_already_actioned_rows(tmp_path):
    """Live report 2026-09-05: the banner said "1 нових вакансій з високим
    скором!" for a vacancy a concurrent session had already applied to
    between two syncs — the count didn't check the same new/interesting-only
    visibility the default list enforces (2026-09-02), so it pointed at a
    row the user could never actually find under the banner."""
    conn = _make_conn(tmp_path)
    _insert_vacancy(conn, "high-but-applied")
    db.set_score(conn, "high-but-applied", 80, {})
    db.set_user_status(conn, "high-but-applied", "applied")
    _insert_vacancy(conn, "high-and-new")
    db.set_score(conn, "high-and-new", 80, {})
    _insert_vacancy(conn, "high-and-interesting")
    db.set_score(conn, "high-and-interesting", 80, {})
    db.set_user_status(conn, "high-and-interesting", "interesting")

    assert db.count_new_high_score(conn, since="2020-01-01 00:00:00", min_score=55) == 2


def test_rows_needing_full_description_retries_after_cooldown(tmp_path):
    conn = _make_conn(tmp_path)
    _insert_vacancy(conn, "tried-long-ago", description="short", source="jobbnorge")
    db.mark_description_fetch_attempted(conn, "tried-long-ago")
    conn.execute(
        "UPDATE vacancies SET description_fetch_attempted_at = datetime('now', '-48 hours') WHERE uuid = ?",
        ("tried-long-ago",),
    )
    conn.commit()

    needing = {r["uuid"] for r in db.rows_needing_full_description(conn, source="jobbnorge", retry_after_hours=24)}
    assert needing == {"tried-long-ago"}


def test_strip_html_preserves_block_boundaries_as_newlines():
    """2026-08-29: <li>/<p>/<br> used to be dropped like any other tag,
    flattening a bullet list into one run-on string — a softener from one
    bullet could then bleed into an unrelated neighboring bullet. Block
    tags now become a newline so hard_blocks.py's clause/section-aware
    checks can tell bullets apart."""
    html = "<ul><li>Krav A</li><li>Krav B</li></ul><p>Ny seksjon</p>"
    result = db.strip_html(html)
    assert "\n" in result
    lines = [l.strip() for l in result.split("\n") if l.strip()]
    assert lines == ["Krav A", "Krav B", "Ny seksjon"]


def test_strip_html_decodes_entities():
    """2026-08-29: "4&#43; years" never matched a plain "4+ years" pattern
    — found in 42% of the live corpus (4503/10768 active ads)."""
    assert db.strip_html("4&#43; years &amp; counting") == "4+ years & counting"
