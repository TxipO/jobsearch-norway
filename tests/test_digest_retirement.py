"""2026-10-05: finn/LinkedIn rows never retired (digest upserts force
status='ACTIVE', application_due=None), Gmail was re-read unbounded, and
Jobbnorge rows were never deactivated. Covers db.retire_stale_digest_rows,
gmail_client.digest_query and jobbnorge_client's snapshot deactivation."""

import db
import finn_client
import gmail_client
import jobbnorge_client as jc
import linkedin_client


def _conn(tmp_path):
    return db.connect(tmp_path / "t.db")


def _row(uuid, title="Selger"):
    return {"uuid": uuid, "status": "ACTIVE", "title": title, "business_name": "Acme AS",
            "municipal": "Oslo", "description": None, "application_url": "https://x", "link": "https://x"}


def _add(conn, uuid, source, age_days, user_status="new"):
    db.upsert_vacancy_row(conn, _row(uuid), source=source)
    conn.execute(
        "UPDATE vacancies SET first_seen_at = datetime('now', ?), user_status = ? WHERE uuid = ?",
        (f"-{age_days} days", user_status, uuid),
    )
    conn.commit()


# --- item 1: retire_stale_digest_rows -------------------------------------

def test_retires_only_old_untouched_digest_rows(tmp_path):
    conn = _conn(tmp_path)
    _add(conn, "finn-old", "finn", 90)
    _add(conn, "linkedin-old", "linkedin", 90)
    _add(conn, "finn-fresh", "finn", 5)
    _add(conn, "nav-old", "nav", 90)
    _add(conn, "finn-applied", "finn", 90, user_status="applied")
    _add(conn, "finn-interesting", "finn", 90, user_status="interesting")
    _add(conn, "finn-flagged", "finn", 90)
    conn.execute("UPDATE vacancies SET flagged_at = datetime('now') WHERE uuid = 'finn-flagged'")
    _add(conn, "finn-noted", "finn", 90)
    conn.execute("UPDATE vacancies SET notes = 'sent via referral' WHERE uuid = 'finn-noted'")

    assert db.retire_stale_digest_rows(conn) == 2

    left = {r[0] for r in conn.execute("SELECT uuid FROM vacancies")}
    assert left == {"finn-fresh", "nav-old", "finn-applied", "finn-interesting", "finn-flagged", "finn-noted"}


def test_retired_row_is_tombstoned_and_not_resurrected_by_digest_reread(tmp_path):
    conn = _conn(tmp_path)
    _add(conn, "finn-old", "finn", 90)
    db.retire_stale_digest_rows(conn)
    assert db.is_dismissed(conn, "finn-old")

    # The digest still mentions the ad: the upsert must not bring it back.
    assert db.upsert_vacancy_row(conn, _row("finn-old"), source="finn") is False
    assert db.get_vacancy(conn, "finn-old") is None


def test_retirement_threshold_is_the_argument(tmp_path):
    conn = _conn(tmp_path)
    _add(conn, "finn-a", "finn", 10)
    assert db.retire_stale_digest_rows(conn, max_age_days=30) == 0
    assert db.retire_stale_digest_rows(conn, max_age_days=5) == 1


def test_trigger_sync_summary_reports_retired_digest(tmp_path, monkeypatch):
    from web import app as web_app
    conn = _conn(tmp_path)
    _add(conn, "finn-old", "finn", 90)
    monkeypatch.setattr(web_app, "get_conn", lambda: conn)
    monkeypatch.setattr(db, "backup_db", lambda: None)
    for mod in (web_app.nav_client, web_app.jobbnorge_client, web_app.finn_client,
                web_app.easycruit_client, web_app.linkedin_client):
        monkeypatch.setattr(mod, "sync", lambda c: {})

    assert web_app.trigger_sync()["retired_digest"] == 1


def test_manually_added_linkedin_row_is_not_retired(tmp_path, monkeypatch):
    """Regression (2026-10-05): a LinkedIn link added via "+ Додати вакансію"
    is stored as source='linkedin', user_status='new' — same as a digest row —
    and was deleted + tombstoned after 60 days."""
    from web import app as web_app
    real_connect = db.connect
    conn = real_connect(tmp_path / "t.db")
    monkeypatch.setattr(web_app.db, "connect", lambda *a, **kw: real_connect(tmp_path / "t.db"))
    web_app.add_vacancy_submit(
        link="https://www.linkedin.com/jobs/view/4459840887/", title="Support", business_name="",
        municipal="", county="", description="", application_due="", user_status="new",
    )
    _add(conn, "linkedin-digest", "linkedin", 90)
    conn.execute("UPDATE vacancies SET first_seen_at = datetime('now', '-90 days') "
                 "WHERE uuid = 'linkedin-4459840887'")
    conn.commit()
    # A later digest re-read of the same id must not clear the marker.
    db.upsert_vacancy_row(conn, _row("linkedin-4459840887"), source="linkedin")

    assert db.retire_stale_digest_rows(conn) == 1

    assert db.get_vacancy(conn, "linkedin-4459840887") is not None
    assert not db.is_dismissed(conn, "linkedin-4459840887")
    assert db.get_vacancy(conn, "linkedin-digest") is None


def test_marker_migration_backfills_only_rows_with_user_typed_content(tmp_path):
    """Pre-marker DB: LinkedIn rows with their own description or deadline can
    only have been typed in (digest rows have neither) and get the marker."""
    path = tmp_path / "old.db"
    conn = db.connect(path)
    for uuid, desc, due, borrowed in [
        ("linkedin-typed-desc", "Egen beskrivelse", None, None),
        ("linkedin-typed-due", None, "2026-12-01", None),
        ("linkedin-digest", None, None, None),
        ("linkedin-borrowed", "Lånt tekst", None, "nav-1"),
    ]:
        db.upsert_vacancy_row(conn, {**_row(uuid), "description": desc, "application_due": due}, source="linkedin")
        conn.execute("UPDATE vacancies SET description_borrowed_from = ? WHERE uuid = ?", (borrowed, uuid))
    conn.execute("ALTER TABLE vacancies DROP COLUMN manually_added")
    conn.commit()
    conn.close()

    conn = db.connect(path)
    marked = {r[0] for r in conn.execute("SELECT uuid FROM vacancies WHERE manually_added = 1")}
    assert marked == {"linkedin-typed-desc", "linkedin-typed-due"}


# --- item 2: bounded Gmail query ------------------------------------------

def test_digest_clients_bound_their_gmail_query(monkeypatch):
    seen = []
    monkeypatch.setattr(finn_client, "fetch_plain_texts", lambda q: seen.append(q) or [])
    monkeypatch.setattr(linkedin_client, "fetch_plain_texts", lambda q: seen.append(q) or [])
    finn_client.fetch_digest_texts()
    linkedin_client.fetch_digest_texts()
    n = gmail_client.GMAIL_LOOKBACK_DAYS
    assert seen == [f"from:finn.no newer_than:{n}d",
                    f"from:jobalerts-noreply@linkedin.com newer_than:{n}d"]


def test_gmail_lookback_never_exceeds_row_retirement_age():
    """A retired row's mail must already be outside the lookback window, so
    nothing re-reads (and could resurrect) it even before the tombstone."""
    assert gmail_client.GMAIL_LOOKBACK_DAYS <= db.DIGEST_ROW_MAX_AGE_DAYS


# --- item 3: Jobbnorge deactivation ---------------------------------------

def _job(i):
    return {"id": i, "title": "IT-konsulent", "employer": "X", "location": "Oslo",
            "summary": "Short summary.", "link": "https://x", "deadline": None,
            "jobDuration": "Fast", "jobScope": "Heltid", "isInternal": False}


def _snapshot(ids, complete=True):
    snap = jc.JobSnapshot(_job(i) for i in ids)
    snap.complete = complete
    return snap


def _sync(conn, monkeypatch, snapshot):
    monkeypatch.setattr(jc, "fetch_all_jobs", lambda: snapshot)
    monkeypatch.setattr(jc, "_build_municipality_county_map", lambda: {})
    monkeypatch.setattr(jc, "backfill_full_descriptions", lambda c: 0)
    return jc.sync(conn)


def _status(conn, i):
    return db.get_vacancy(conn, f"jobbnorge-{i}")["status"]


def test_missing_jobbnorge_rows_are_marked_inactive_then_reaped(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    _sync(conn, monkeypatch, _snapshot(range(1, 21)))
    db.set_user_status(conn, "jobbnorge-20", "interesting")

    stats = _sync(conn, monkeypatch, _snapshot(range(1, 19)))  # 19 and 20 gone

    assert stats["marked_inactive"] == 2 and "warning" not in stats
    assert _status(conn, 19) == "INACTIVE" and _status(conn, 20) == "INACTIVE"
    assert _status(conn, 1) == "ACTIVE"
    assert db.delete_inactive(conn) == 1  # the untouched one; the reacted row is kept
    assert db.get_vacancy(conn, "jobbnorge-19") is None
    assert db.get_vacancy(conn, "jobbnorge-20") is not None


def test_reappearing_jobbnorge_row_is_active_again(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    _sync(conn, monkeypatch, _snapshot(range(1, 21)))
    _sync(conn, monkeypatch, _snapshot(range(1, 20)))
    assert _status(conn, 20) == "INACTIVE"
    _sync(conn, monkeypatch, _snapshot(range(1, 21)))
    assert _status(conn, 20) == "ACTIVE"


def test_capped_snapshot_deactivates_nothing_and_warns(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    _sync(conn, monkeypatch, _snapshot(range(1, 21)))
    stats = _sync(conn, monkeypatch, _snapshot(range(1, 19), complete=False))
    assert stats["marked_inactive"] == 0 and "incomplete" in stats["warning"]
    assert _status(conn, 20) == "ACTIVE"


def test_empty_snapshot_deactivates_nothing_and_warns(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    _sync(conn, monkeypatch, _snapshot(range(1, 21)))
    stats = _sync(conn, monkeypatch, _snapshot([]))
    assert stats["marked_inactive"] == 0 and "warning" in stats
    assert _status(conn, 1) == "ACTIVE"


def test_suspiciously_small_snapshot_deactivates_nothing_and_warns(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    _sync(conn, monkeypatch, _snapshot(range(1, 21)))
    stats = _sync(conn, monkeypatch, _snapshot(range(1, 8)))  # 7/20 survive < 50%
    assert stats["marked_inactive"] == 0 and "warning" in stats
    assert all(_status(conn, i) == "ACTIVE" for i in range(1, 21))


def test_fetch_all_jobs_marks_complete_only_when_pagination_ends(monkeypatch):
    class R:
        def __init__(self, payload): self._p = payload
        def raise_for_status(self): pass
        def json(self): return self._p

    pages = [[{"id": 1}] * jc.PAGE_SIZE, [{"id": 2}]]
    monkeypatch.setattr(jc.requests, "get", lambda *a, **k: R(pages.pop(0)))
    assert jc.fetch_all_jobs().complete is True

    monkeypatch.setattr(jc.requests, "get", lambda *a, **k: R([{"id": 1}] * jc.PAGE_SIZE))
    assert jc.fetch_all_jobs().complete is False  # MAX_PAGES cap
