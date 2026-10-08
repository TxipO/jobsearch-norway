"""Tests for easycruit_client.py — parsing verified against a real captured
page (Sogndal kommune, "Aktivitetsguide", 2026-07-19); sync() orchestration
mocked out per test_jobbnorge_client.py's pattern, no real network calls
here (that's covered by the manual verification that shaped this module —
see its docstring for why the list page can't be hit at all)."""

from datetime import datetime, timedelta, timezone

import requests

import db
import easycruit_client as ec

# A trimmed-but-structurally-real fragment of the actual detail page HTML —
# same class names/nesting as the live site, just with a short description
# instead of the full multi-section one.
SAMPLE_HTML = """
<html><head>
<title>Sogndal kommune Oppvekst - Aktivitetsguide</title>
<meta property="og:title" content="Aktivitetsguide">
<meta property="og:description" content="Sogndal kommune Oppvekst">
</head><body>
<div class="jd-description"><p>Vil du bidra til at fleire barn og unge får oppleve meistring?</p><ul type="disc"><li>Førarkort klasse B</li></ul></div>
<div class="bottom-buttons"></div>
<div class="jd-counties">
    <h3>Fylke:</h3>
    <ul>					<li>Vestland</li>
    </ul>
</div>
<div class="jd-type">
    <h3>Jobbtype:</h3>
    <p>Engasjement</p>
</div>
<div class="jd-workhours">
    <h3>Heiltid/Deltid:</h3>
    <p>Heiltid</p>
</div>
<div class="jd-deadline">
    <h3>Søknadsfrist:</h3>
    <p>02.08.2026</p>
</div>
<div class="jd-location">
    <h3>Arbeidsstad:</h3>
    <p>Sogndal</p>
</div>
</body></html>
"""


class FakeResponse:
    """content-only, deliberately no working .text — the server's real
    Content-Type header omits a charset, so requests defaults resp.encoding
    to ISO-8859-1 (RFC 2616) even though the page is actually UTF-8. Live
    bug caught 2026-07-19: fetch_vacancy_detail used to read resp.text and
    silently mojibake every æøå ("på" round-tripped as "pÃ¥") straight into
    the DB. fetch_vacancy_detail must decode resp.content itself, not
    trust resp.text — this fake has no working .text at all so the test
    fails loudly if the fix regresses back to using it."""
    status_code = 200

    def __init__(self, html: str):
        self.content = html.encode("utf-8")

    def raise_for_status(self):
        pass


def _make_conn(tmp_path):
    return db.connect(tmp_path / "test.db")


def test_to_iso_date_converts_norwegian_format():
    assert ec._to_iso_date("02.08.2026") == "2026-08-02"


def test_to_iso_date_none_when_missing_or_malformed():
    assert ec._to_iso_date(None) is None
    assert ec._to_iso_date("not a date") is None


def test_fetch_vacancy_detail_parses_real_page_structure(monkeypatch):
    """Regex patterns must match the actual live site's markup, not just
    something plausible-looking — this fixture is a trimmed copy of a real
    captured page, not hand-invented HTML."""
    monkeypatch.setattr(ec.requests, "get", lambda *a, **k: FakeResponse(SAMPLE_HTML))

    row = ec.fetch_vacancy_detail("3643525", "189801")

    assert row["uuid"] == "easycruit-sogndal-3643525"
    assert row["title"] == "Aktivitetsguide"
    assert "meistring" in row["description"]
    assert "Førarkort klasse B" in row["description"]  # so car_penalty can catch it
    assert row["municipal"] == "Sogndal"
    assert row["county"] == "Vestland"
    assert row["application_due"] == "2026-08-02"
    assert row["extent"] == "Heiltid"
    assert row["business_name"] == "Sogndal kommune"


def test_fetch_vacancy_detail_decodes_norwegian_characters_correctly(monkeypatch):
    """Live bug 2026-07-19: without decoding resp.content as UTF-8
    explicitly, "på leikelaget" mojibaked into "pÃ¥ leikelaget" straight in
    the DB, for every title/description containing æ/ø/å."""
    html = SAMPLE_HTML.replace(
        '<meta property="og:title" content="Aktivitetsguide">',
        '<meta property="og:title" content="Barnehagelærar – bli med på leikelaget!">',
    )
    monkeypatch.setattr(ec.requests, "get", lambda *a, **k: FakeResponse(html))

    row = ec.fetch_vacancy_detail("1", "1")
    assert row["title"] == "Barnehagelærar – bli med på leikelaget!"


def test_fetch_vacancy_detail_unescapes_html_entities_in_text_fields(monkeypatch):
    """2026-10-05: og:title / location / county come out of HTML attributes
    and elements still entity-encoded; the UI escapes again, so "Rådgiver &
    saksbehandler" showed as "&amp;". Same shape as b1430d1's LinkedIn fix."""
    html = (
        SAMPLE_HTML
        .replace('content="Aktivitetsguide"', 'content="Rådgiver &amp; saksbehandler &quot;X&quot;"')
        .replace("<p>Sogndal</p>", "<p>Sogndal &amp; Luster</p>")
        .replace("<li>Vestland</li>", "<li>Møre &amp; Romsdal</li>")
    )
    monkeypatch.setattr(ec.requests, "get", lambda *a, **k: FakeResponse(html))

    row = ec.fetch_vacancy_detail("1", "1")

    assert row["title"] == 'Rådgiver & saksbehandler "X"'
    assert row["municipal"] == "Sogndal & Luster"
    assert row["county"] == "Møre & Romsdal"


def test_fetch_vacancy_detail_returns_none_on_malformed_page(monkeypatch):
    monkeypatch.setattr(
        ec.requests, "get",
        lambda *a, **k: FakeResponse("<html><body>Not a vacancy page</body></html>"),
    )

    assert ec.fetch_vacancy_detail("999", "999") is None


def test_known_ids_roundtrip(tmp_path):
    conn = _make_conn(tmp_path)
    assert ec.get_known_ids(conn) == []

    ec.set_known_ids(conn, [("3643525", "189801"), ("3644035", "209579")])
    assert ec.get_known_ids(conn) == [("3643525", "189801"), ("3644035", "209579")]


def test_sync_upserts_each_known_id(tmp_path, monkeypatch):
    conn = _make_conn(tmp_path)
    ec.set_known_ids(conn, [("111", "1"), ("222", "2")])

    fake_rows = {
        ("111", "1"): {"uuid": "easycruit-sogndal-111", "status": "ACTIVE", "title": "Job A",
                        "description": "A real description long enough to matter here today.",
                        "municipal": "Sogndal", "county": "Vestland", "business_name": "Sogndal kommune",
                        "employer_name": "Sogndal kommune", "application_url": "https://x", "link": "https://x",
                        "application_due": None, "engagement_type": None, "extent": None, "sector": None},
        ("222", "2"): None,  # simulates a malformed/withdrawn listing
    }
    monkeypatch.setattr(ec, "fetch_vacancy_detail", lambda vid, did: fake_rows[(vid, did)])

    result = ec.sync(conn)

    assert {k: result[k] for k in ("known", "fetched", "failed")} == {"known": 2, "fetched": 1, "failed": 1}
    assert "1 of 2" in result["warning"]
    assert db.get_vacancy(conn, "easycruit-sogndal-111")["title"] == "Job A"
    assert db.get_vacancy(conn, "easycruit-sogndal-222") is None


def test_sync_survives_a_network_failure_on_one_id(tmp_path, monkeypatch):
    conn = _make_conn(tmp_path)
    ec.set_known_ids(conn, [("111", "1")])

    def raise_error(vid, did):
        raise requests.RequestException("timeout")

    monkeypatch.setattr(ec, "fetch_vacancy_detail", raise_error)

    result = ec.sync(conn)
    assert {k: result[k] for k in ("known", "fetched", "failed")} == {"known": 1, "fetched": 0, "failed": 1}


def test_sync_is_silent_when_the_list_is_fresh_and_everything_fetches(tmp_path, monkeypatch):
    conn = _make_conn(tmp_path)
    ec.set_known_ids(conn, [("111", "1")])
    row = {"uuid": "easycruit-sogndal-111", "status": "ACTIVE", "title": "Job A",
           "description": "A real description long enough to matter here today.",
           "municipal": "Sogndal", "county": "Vestland", "business_name": "Sogndal kommune",
           "employer_name": "Sogndal kommune", "application_url": "https://x", "link": "https://x",
           "application_due": None, "engagement_type": None, "extent": None, "sector": None}
    monkeypatch.setattr(ec, "fetch_vacancy_detail", lambda vid, did: row)
    assert ec.sync(conn) == {"known": 1, "fetched": 1, "failed": 0}


def test_sync_warns_when_the_hand_maintained_list_has_gone_stale(tmp_path, monkeypatch):
    """2026-10-08: the id list sat untouched for two months while Sogndal posted
    four new vacancies (the list page is behind a WAF challenge, so nothing can
    discover them automatically) — and the only symptom was "failed: 8" for the
    closed ones. A list older than IDS_STALE_AFTER_DAYS is now reported."""
    conn = _make_conn(tmp_path)
    ec.set_known_ids(conn, [])
    old = (datetime.now(timezone.utc) - timedelta(days=ec.IDS_STALE_AFTER_DAYS + 1)).strftime("%Y-%m-%d %H:%M:%S")
    db.set_state(conn, ec.IDS_REFRESHED_AT_KEY, old)
    assert "last refreshed" in ec.sync(conn)["warning"]

    ec.set_known_ids(conn, [])  # refreshing the list clears it
    assert "warning" not in ec.sync(conn)


def test_sync_warns_when_the_list_has_no_refresh_date_at_all(tmp_path):
    """A database from before this timestamp existed: treat unknown as stale."""
    conn = _make_conn(tmp_path)
    db.set_state(conn, ec.KNOWN_IDS_KEY, "[]")
    assert "never" in ec.sync(conn)["warning"]
