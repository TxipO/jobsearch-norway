"""Regression test for a live bug (2026-07-17): jobbnorge_client.sync()
always re-upserted the short `summary` text over an already-backfilled full
description, so every single sync silently wiped out the previous sync's
description backfill — making "Sync now" take 2-4+ minutes on every click
instead of only when there's genuinely new content to fetch."""

import db
import jobbnorge_client as jc


def test_parse_extent_percent_recognizes_nynorsk_heiltid():
    """code-review 2026-07-19: only Bokmål 'Heltid' was recognized, but
    this function is shared by every source's scoring pass — easycruit_client.py
    explicitly requests Nynorsk pages (iso=nn), whose full-time field reads
    'Heiltid', silently falling through to None (no '(100%)' badge) for
    every genuinely full-time Sogndal kommune posting."""
    assert jc._parse_extent_percent("Heltid", "", "") == 100
    assert jc._parse_extent_percent("Heiltid", "", "") == 100


def test_sync_preserves_already_backfilled_description(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "test.db")

    # Seed a row exactly as it would look after a prior sync + successful
    # description backfill: long, real text, not the ~90-char `summary`.
    full_description = "A " * 200  # 400 chars, well past the 300-char threshold
    db.upsert_vacancy_row(
        conn,
        {
            "uuid": "jobbnorge-123",
            "status": "ACTIVE",
            "title": "IT-konsulent",
            "description": full_description,
        },
        source="jobbnorge",
    )

    # Simulate the next sync's nationwide fetch returning the SAME job, but
    # the documented API only ever gives the short summary for this field.
    monkeypatch.setattr(
        jc, "fetch_all_jobs",
        lambda: [{"id": 123, "title": "IT-konsulent", "employer": "X", "location": "Oslo",
                   "summary": "Short summary.", "link": "https://x", "deadline": None,
                   "jobDuration": "Fast", "jobScope": "Heltid", "isInternal": False}],
    )
    monkeypatch.setattr(jc, "_build_municipality_county_map", lambda: {})
    # Isolate this test from backfill_full_descriptions' own network calls —
    # that's covered by manual verification, not a unit test concern here.
    monkeypatch.setattr(jc, "backfill_full_descriptions", lambda conn: 0)

    jc.sync(conn)

    row = db.get_vacancy(conn, "jobbnorge-123")
    assert row["description"] == full_description, (
        "sync() overwrote an already-backfilled full description with the short summary"
    )
