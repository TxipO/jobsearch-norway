"""health.evaluate_sources / stale_code. Every rule below is tied to a failure
that really happened and raised no error: NAV's cursor stuck on an ETag
(2026-08-31, "+0 new" for two days), LinkedIn's parser dropping 89% of the jobs
(weeks), EasyCruit's hand-kept id list going stale (two months), and a server
still running yesterday's code."""

import os
from datetime import datetime, timedelta, timezone

import db
import health

NOW = datetime(2026, 10, 8, 12, 0, 0, tzinfo=timezone.utc)
SYNC_AT = "2026-10-08 10:00:00"  # last sync: two hours ago


def _conn(tmp_path):
    return db.connect(tmp_path / "t.db")


def _row(conn, uuid, source, first_seen):
    db.upsert_vacancy_row(conn, {
        "uuid": uuid, "status": "ACTIVE", "title": "T", "business_name": "B", "municipal": "Oslo",
        "description": None, "application_url": "https://x", "link": "https://x",
    }, source=source)
    conn.execute("UPDATE vacancies SET first_seen_at = ? WHERE uuid = ?", (first_seen, uuid))
    conn.commit()


def _healthy_conn(tmp_path):
    """Every freshness-checked source got a new row an hour before the sync."""
    conn = _conn(tmp_path)
    for source in ("nav", "finn", "linkedin", "jobbnorge"):
        _row(conn, f"{source}-1", source, "2026-10-08 09:00:00")
    return conn


def _summary(**over):
    s = {
        "watermark_utc": SYNC_AT,
        "stats": {"pages": 1, "new": 40, "updated": 3, "unchanged": 5, "marked_inactive": 2, "detail_missing": 0, "detail_errors": 0},
        "jobbnorge": {"fetched": 1000, "marked_inactive": 20},
        "finn": {"messages": 50, "cards": 500, "parsed": 500, "upserted": 500},
        "easycruit": {"known": 9, "fetched": 9, "failed": 0},
        "linkedin": {"messages": 60, "cards": 200, "parsed": 200, "upserted": 10},
    }
    s.update(over)
    return s


def _by_source(issues):
    return {i["source"]: i for i in issues}


def test_healthy_pipeline_reports_nothing(tmp_path):
    assert health.evaluate_sources(_healthy_conn(tmp_path), _summary(), now=NOW) == []


def test_no_summary_yet_reports_nothing(tmp_path):
    assert health.evaluate_sources(_conn(tmp_path), None, now=NOW) == []


def test_nav_cursor_stall_is_caught_on_the_second_day(tmp_path):
    """2026-08-31: every sync succeeded and said "+0 new"; the only zero day in
    30 was the stall itself. 2 days without a new NAV row = warn, 4 = bad."""
    conn = _healthy_conn(tmp_path)
    conn.execute("UPDATE vacancies SET first_seen_at = '2026-10-06 09:00:00' WHERE uuid = 'nav-1'")
    nav = _by_source(health.evaluate_sources(conn, _summary(), now=NOW))["NAV"]
    assert nav["level"] == "warn" and "зависання курсора" in nav["message"] and nav["action"]

    conn.execute("UPDATE vacancies SET first_seen_at = '2026-10-04 09:00:00' WHERE uuid = 'nav-1'")
    assert _by_source(health.evaluate_sources(conn, _summary(), now=NOW))["NAV"]["level"] == "bad"


def test_a_quiet_nav_day_is_not_an_alarm(tmp_path):
    conn = _healthy_conn(tmp_path)
    conn.execute("UPDATE vacancies SET first_seen_at = '2026-10-07 02:00:00' WHERE uuid = 'nav-1'")  # ~1.3 days
    assert health.evaluate_sources(conn, _summary(), now=NOW) == []


def test_staleness_is_measured_against_the_last_sync_not_against_today(tmp_path):
    """A week without syncing is one problem ("Sync"), not five: the sources
    were all fine at the moment of the last sync."""
    conn = _healthy_conn(tmp_path)
    issues = health.evaluate_sources(conn, _summary(), now=NOW + timedelta(days=8))
    assert [i["source"] for i in issues] == ["Sync"]
    assert "8 дн." in issues[0]["message"]


def test_source_error_is_bad_and_skips_its_other_rules(tmp_path):
    conn = _conn(tmp_path)  # no rows at all: freshness would otherwise be unknowable
    issues = health.evaluate_sources(conn, _summary(linkedin={"error": "IMAP login failed: bad password"}), now=NOW)
    li = _by_source(issues)["LinkedIn"]
    assert li["level"] == "bad" and "IMAP login failed" in li["message"]
    assert sum(1 for i in issues if i["source"] == "LinkedIn") == 1


def test_linkedin_parse_gap_is_bad(tmp_path):
    """2026-10-08: 78% of the mails parsed to nothing for weeks; the total never
    reached 0, so the old "0 parsed" warning never fired. The client now counts
    cards independently and puts that into stats["warning"]."""
    conn = _healthy_conn(tmp_path)
    gap = {"messages": 60, "cards": 200, "parsed": 40, "upserted": 5,
           "warning": "160 of 200 LinkedIn job card(s) could not be parsed"}
    li = _by_source(health.evaluate_sources(conn, _summary(linkedin=gap), now=NOW))["LinkedIn"]
    assert li["level"] == "bad" and "160 of 200" in li["message"]


def test_easycruit_stale_list_warning_is_only_a_warn(tmp_path):
    """The hand-kept id list had not been refreshed for two months; the only
    symptom was "failed: 8"."""
    conn = _healthy_conn(tmp_path)
    ec = {"known": 13, "fetched": 5, "failed": 8, "warning": "8 of 13 stored vacancies could not be fetched"}
    issue = _by_source(health.evaluate_sources(conn, _summary(easycruit=ec), now=NOW))["EasyCruit"]
    assert issue["level"] == "warn"


def test_mail_source_with_no_mail_at_all_is_one_bad_issue_not_two(tmp_path):
    """Gmail forwarding/filter broke: no digest in the whole search window. The
    freshness rule would say the same thing worse, so it stays quiet."""
    conn = _healthy_conn(tmp_path)
    conn.execute("UPDATE vacancies SET first_seen_at = '2026-08-01 09:00:00' WHERE uuid = 'finn-1'")
    issues = health.evaluate_sources(conn, _summary(finn={"messages": 0, "cards": 0, "parsed": 0, "upserted": 0}), now=NOW)
    finn = [i for i in issues if i["source"] == "finn.no"]
    assert len(finn) == 1 and finn[0]["level"] == "bad" and "листа-дайджесту" in finn[0]["message"]


def test_finn_going_quiet_is_caught_by_freshness(tmp_path):
    conn = _healthy_conn(tmp_path)
    conn.execute("UPDATE vacancies SET first_seen_at = '2026-10-04 09:00:00' WHERE uuid = 'finn-1'")  # 4 days
    finn = _by_source(health.evaluate_sources(conn, _summary(), now=NOW))["finn.no"]
    assert finn["level"] == "warn" and "Gmail" in finn["message"]


def test_nav_detail_errors_are_surfaced_as_a_paused_import(tmp_path):
    conn = _healthy_conn(tmp_path)
    stats = {"pages": 1, "new": 0, "updated": 0, "unchanged": 0, "marked_inactive": 0, "detail_missing": 0, "detail_errors": 3}
    nav = _by_source(health.evaluate_sources(conn, _summary(stats=stats), now=NOW))["NAV"]
    assert nav["level"] == "warn" and "3 оголошень" in nav["message"] and "призупинено" in nav["message"]


def test_jobbnorge_empty_snapshot_is_bad_and_mass_deactivation_is_warn(tmp_path):
    conn = _healthy_conn(tmp_path)
    empty = health.evaluate_sources(conn, _summary(jobbnorge={"fetched": 0, "marked_inactive": 0}), now=NOW)
    assert _by_source(empty)["Jobbnorge"]["level"] == "bad"
    mass = health.evaluate_sources(conn, _summary(jobbnorge={"fetched": 600, "marked_inactive": 500}), now=NOW)
    assert _by_source(mass)["Jobbnorge"]["level"] == "warn"
    normal = health.evaluate_sources(conn, _summary(jobbnorge={"fetched": 1000, "marked_inactive": 70}), now=NOW)
    assert "Jobbnorge" not in _by_source(normal)


def test_source_with_no_rows_yet_is_not_flagged_for_freshness(tmp_path):
    """A fresh database (or a source that never delivered) has nothing to age."""
    conn = _conn(tmp_path)
    assert health.evaluate_sources(conn, _summary(), now=NOW) == []


def test_bad_issues_sort_before_warnings(tmp_path):
    conn = _healthy_conn(tmp_path)
    conn.execute("UPDATE vacancies SET first_seen_at = '2026-10-06 09:00:00' WHERE uuid = 'nav-1'")  # warn
    issues = health.evaluate_sources(conn, _summary(linkedin={"error": "boom"}), now=NOW)
    assert [i["level"] for i in issues] == ["bad", "warn"]


def test_stale_code_fires_only_when_a_python_file_is_newer_than_the_process(tmp_path):
    """Without --reload every Python edit needs a restart, and nothing says so:
    the single most repeated "why isn't my change showing" of this project."""
    (tmp_path / "web").mkdir()
    old = tmp_path / "scoring.py"
    old.write_text("x = 1")
    os.utime(old, (1_000_000, 1_000_000))
    assert health.stale_code(started_at=2_000_000, root=tmp_path) is None

    edited = tmp_path / "web" / "app.py"
    edited.write_text("x = 2")
    os.utime(edited, (3_000_000, 3_000_000))
    assert health.stale_code(started_at=2_000_000, root=tmp_path) is not None


def test_stale_code_ignores_non_python_files(tmp_path):
    """Templates and CSS are re-read per request; only .py needs a restart."""
    (tmp_path / "web").mkdir()
    css = tmp_path / "web" / "style.css"
    css.write_text("a{}")
    os.utime(css, (3_000_000, 3_000_000))
    assert health.stale_code(started_at=2_000_000, root=tmp_path) is None
