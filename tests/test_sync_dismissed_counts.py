"""Review 2026-10-05: upsert_vacancy_row returns False for a tombstoned
(trashed + deleted) uuid, but the finn/linkedin/easycruit/jobbnorge sync
counters counted every call as written. A skipped row must not be tallied."""

import db
import easycruit_client as ec
import finn_client
import jobbnorge_client as jc
import linkedin_client
from test_finn_client import _digest
from test_linkedin_client import REAL_DIGEST_EXCERPT


def _tombstone(conn, *uuids):
    for u in uuids:
        conn.execute("INSERT INTO dismissed_vacancies (uuid) VALUES (?)", (u,))
    conn.commit()


def test_finn_sync_does_not_count_tombstoned_row(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "t.db")
    monkeypatch.setattr(finn_client, "_build_municipality_county_map", lambda: {})
    ok = _digest("Selger\nNordspec AS, Laksevåg\nFlere detaljer: https://www.finn.no/111")
    monkeypatch.setattr(finn_client, "fetch_digest_texts", lambda: [ok])
    _tombstone(conn, "finn-111")
    stats = finn_client.sync(conn)
    assert stats["parsed"] == 1 and stats["upserted"] == 0
    assert db.get_vacancy(conn, "finn-111") is None


def test_linkedin_sync_does_not_count_tombstoned_rows(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "t.db")
    monkeypatch.setattr(linkedin_client, "_build_municipality_county_map", lambda: {})
    monkeypatch.setattr(linkedin_client, "fetch_digest_texts", lambda: [REAL_DIGEST_EXCERPT])
    first = linkedin_client.sync(conn)
    assert first["upserted"] == first["parsed"] > 0
    uuids = [r[0] for r in conn.execute("SELECT uuid FROM vacancies")]
    conn.execute("DELETE FROM vacancies")
    _tombstone(conn, *uuids)
    again = linkedin_client.sync(conn)
    assert again["parsed"] == first["parsed"] and again["upserted"] == 0


def test_easycruit_sync_does_not_count_tombstoned_row(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "t.db")
    ec.set_known_ids(conn, [("111", "1")])
    row = {"uuid": "easycruit-sogndal-111", "status": "ACTIVE", "title": "Job A",
           "description": "A real description long enough to matter here today.",
           "municipal": "Sogndal", "county": "Vestland", "business_name": "Sogndal kommune",
           "employer_name": "Sogndal kommune", "application_url": "https://x", "link": "https://x",
           "application_due": None, "engagement_type": None, "extent": None, "sector": None}
    monkeypatch.setattr(ec, "fetch_vacancy_detail", lambda vid, did: row)
    _tombstone(conn, "easycruit-sogndal-111")
    assert ec.sync(conn) == {"known": 1, "fetched": 0, "failed": 0}


def test_jobbnorge_sync_does_not_count_tombstoned_row(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "t.db")
    jobs = [{"id": i, "title": "IT-konsulent", "employer": "X", "location": "Oslo",
             "summary": "Short summary.", "link": "https://x", "deadline": None,
             "jobDuration": "Fast", "jobScope": "Heltid", "isInternal": False} for i in (1, 2)]
    monkeypatch.setattr(jc, "fetch_all_jobs", lambda: jobs)
    monkeypatch.setattr(jc, "_build_municipality_county_map", lambda: {})
    monkeypatch.setattr(jc, "backfill_full_descriptions", lambda conn: 0)
    _tombstone(conn, "jobbnorge-1")
    assert jc.sync(conn)["fetched"] == 1
    assert db.get_vacancy(conn, "jobbnorge-1") is None
