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


class _R:
    """Minimal requests.Response stand-in."""

    def __init__(self, payload=None, content=b""):
        self._payload = payload
        self.content = content

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_county_api_failure_degrades_to_empty_map(monkeypatch, caplog):
    """_build_municipality_county_map is shared by jobbnorge, finn and
    linkedin; an uncaught county-API error used to kill all three sources
    (2026-10-05 audit). It's optional enrichment -> {} + warning."""
    def boom():
        raise jc.requests.ConnectionError("county API down")

    monkeypatch.setattr(jc, "fetch_counties", boom)
    with caplog.at_level("WARNING"):
        assert jc._build_municipality_county_map() == {}
    assert any("County lookup failed" in r.message for r in caplog.records)

    # Malformed (non-dict) county payload degrades the same way.
    monkeypatch.setattr(jc, "fetch_counties", lambda: ["not-a-dict"])
    assert jc._build_municipality_county_map() == {}


def test_poststed_registry_with_undefined_cp1252_byte_does_not_raise(monkeypatch):
    """0x81 is undefined in cp1252: a strict decode raised UnicodeDecodeError
    and (uncaught) killed every source using the county map."""
    content = b"0001\tOSLO\t0301\tOSLO\n0002\tBAD\x81NAME\t0301\tOSLO\n"
    monkeypatch.setattr(jc.requests, "get", lambda *a, **k: _R(content=content))
    lookup = jc.fetch_poststed_to_municipality()
    assert lookup["OSLO"] == "OSLO"

    # And the map builder survives even if the fetch itself raises.
    monkeypatch.setattr(jc, "fetch_counties", lambda: [{"name": "Oslo", "municipality": []}])

    def bad_decode():
        raise UnicodeDecodeError("cp1252", b"\x81", 0, 1, "undefined")

    monkeypatch.setattr(jc, "fetch_poststed_to_municipality", bad_decode)
    assert jc._build_municipality_county_map() == {"OSLO": "Oslo"}


def test_fetch_full_description_tolerates_non_dict_json(monkeypatch):
    """A list/str JSON body made data.get raise AttributeError and abort the
    whole backfill loop (2026-10-05)."""
    for payload in ([1, 2], "oops", None):
        monkeypatch.setattr(jc.requests, "get", lambda *a, _p=payload, **k: _R(payload=_p))
        assert jc.fetch_full_description(1) is None


def test_fetch_all_jobs_caps_pages_if_api_ignores_page_param(monkeypatch, caplog):
    """If the API ignores `page` and always returns a full page, the loop
    never ended (2026-10-05)."""
    full_page = [{"id": i} for i in range(jc.PAGE_SIZE)]
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(params["page"])
        return _R(payload=full_page)

    monkeypatch.setattr(jc.requests, "get", fake_get)
    with caplog.at_level("WARNING"):
        jobs = jc.fetch_all_jobs()

    assert len(calls) == jc.MAX_PAGES
    assert len(jobs) == jc.MAX_PAGES * jc.PAGE_SIZE
    assert any("pagination stopped" in r.message for r in caplog.records)


def _job(job_id, summary="Short summary that is long enough for language detection to bother."):
    return {"id": job_id, "title": "IT-konsulent", "employer": "X", "location": "Oslo",
            "summary": summary, "link": "https://x", "deadline": None,
            "jobDuration": "Fast", "jobScope": "Heltid", "isInternal": False}


def test_language_is_detected_only_for_new_rows_or_changed_descriptions(tmp_path, monkeypatch):
    """langdetect was 17 s of a 26 s jobbnorge sync: every row re-detected its
    unchanged description on every sync (2026-10-20)."""
    conn = db.connect(tmp_path / "test.db")
    calls = []
    real = db.detect_language
    monkeypatch.setattr(db, "detect_language", lambda text: calls.append(text) or real(text))
    monkeypatch.setattr(jc, "_build_municipality_county_map", lambda: {})
    monkeypatch.setattr(jc, "backfill_full_descriptions", lambda conn: 0)
    jobs = [_job(1), _job(2)]
    monkeypatch.setattr(jc, "fetch_all_jobs", lambda: jobs)

    jc.sync(conn)
    assert len(calls) == 2  # both new
    stored = {r["uuid"]: r["language"] for r in conn.execute("SELECT uuid, language FROM vacancies")}

    jc.sync(conn)
    assert len(calls) == 2, "unchanged descriptions must not be detected again"
    assert {r["uuid"]: r["language"] for r in conn.execute("SELECT uuid, language FROM vacancies")} == stored

    jobs[1] = _job(2, summary="Dette er en helt ny og lengre beskrivelse av stillingen som IT-konsulent.")
    jc.sync(conn)
    assert len(calls) == 3, "only the changed description is detected again"
    assert db.get_vacancy(conn, "jobbnorge-2")["language"] == real(jobs[1]["summary"])


def test_language_with_no_stored_value_is_detected_again(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "test.db")
    text = "Dette er en tilstrekkelig lang beskrivelse av en stilling i Oslo."
    db.upsert_vacancy_row(conn, {"uuid": "jobbnorge-9", "title": "T", "description": text}, source="jobbnorge")
    conn.execute("UPDATE vacancies SET language = NULL WHERE uuid = 'jobbnorge-9'")
    conn.commit()

    db.upsert_vacancy_row(conn, {"uuid": "jobbnorge-9", "title": "T", "description": text}, source="jobbnorge")

    assert db.get_vacancy(conn, "jobbnorge-9")["language"] == db.detect_language(text)


def test_sync_commits_rows_written_before_a_mid_loop_exception(tmp_path, monkeypatch):
    """The loop commits once instead of per row, but a failure part-way must
    still leave the already-written rows persisted (the old per-row behaviour),
    not rolled back or left as an open transaction for the next source."""
    conn = db.connect(tmp_path / "test.db")
    monkeypatch.setattr(jc, "_build_municipality_county_map", lambda: {})
    monkeypatch.setattr(jc, "backfill_full_descriptions", lambda conn: 0)
    bad = _job(3)
    del bad["id"]  # to_vacancy_row raises KeyError
    monkeypatch.setattr(jc, "fetch_all_jobs", lambda: [_job(1), _job(2), bad])

    try:
        jc.sync(conn)
        assert False, "expected KeyError"
    except KeyError:
        pass

    assert not conn.in_transaction
    other = db.connect(tmp_path / "test.db")
    assert {r[0] for r in other.execute("SELECT uuid FROM vacancies")} == {"jobbnorge-1", "jobbnorge-2"}
