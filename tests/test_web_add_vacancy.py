"""Regression tests for the manual "Додати вакансію" add-form (2026-08-30,
user-requested — "чи треба через тебе це робити кожен раз?").

Calls route functions directly (same pattern as test_web_index_filters.py)
rather than going through fastapi.testclient — no new dependency needed to
check plain function behavior.
"""

import db
from starlette.requests import Request

from web import app as web_app


def _setup(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    real_connect = db.connect
    conn = real_connect(db_path)
    monkeypatch.setattr(web_app.db, "connect", lambda *a, **kw: real_connect(db_path))
    return conn


def test_add_vacancy_submit_manual_entry(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    web_app.add_vacancy_submit(
        link="", title="Testrolle", business_name="Acme AS", municipal="Oslo", county="Oslo",
        description="En beskrivelse lang nok til å ikke være et sammendrag.",
        application_due="", user_status="interesting",
    )
    row = conn.execute("SELECT * FROM vacancies WHERE source = 'manual'").fetchone()
    assert row is not None
    assert row["uuid"].startswith("manual-")
    assert row["title"] == "Testrolle"
    assert row["business_name"] == "Acme AS"
    assert row["user_status"] == "interesting"
    # Scored immediately (rescore_one), not left waiting for the next sync.
    assert row["score"] is not None
    assert row["score_it"] is not None


def test_add_vacancy_submit_linkedin_link_derives_uuid(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    web_app.add_vacancy_submit(
        link="https://www.linkedin.com/jobs/view/4459840887/?extra=tracking",
        title="Desktop Support Engineer", business_name="HCLTech", municipal="Trondheim",
        county="Trøndelag", description="", application_due="", user_status="new",
    )
    row = conn.execute("SELECT * FROM vacancies WHERE uuid = 'linkedin-4459840887'").fetchone()
    assert row is not None
    assert row["source"] == "linkedin"
    assert row["application_url"] == "https://www.linkedin.com/jobs/view/4459840887/?extra=tracking"


def test_add_vacancy_submit_blank_description_does_not_clobber_existing(tmp_path, monkeypatch):
    """A LinkedIn row already carrying a borrowed description (from the
    automated digest sync or a previous manual add) must not be wiped back
    to NULL just because this submission left the field blank — same
    COALESCE convention db.upsert_vacancy_row already applies everywhere
    else (finn.no rows, etc.)."""
    conn = _setup(tmp_path, monkeypatch)
    web_app.add_vacancy_submit(
        link="https://www.linkedin.com/jobs/view/1111111111/",
        title="Some Role", business_name="Some AS", municipal="Oslo", county="Oslo",
        description="Real description text, long enough to count as real content here.",
        application_due="", user_status="new",
    )
    web_app.add_vacancy_submit(
        link="https://www.linkedin.com/jobs/view/1111111111/",
        title="Some Role", business_name="Some AS", municipal="Oslo", county="Oslo",
        description="", application_due="", user_status="new",
    )
    row = conn.execute("SELECT description FROM vacancies WHERE uuid = 'linkedin-1111111111'").fetchone()
    assert row["description"] == "Real description text, long enough to count as real content here."


def test_linkedin_job_view_url_re_matches_direct_browser_urls():
    for url in (
        "https://www.linkedin.com/jobs/view/4459840887/",
        "https://www.linkedin.com/jobs/view/desktop-support-engineer-at-hcltech-4459840887",
        "https://no.linkedin.com/jobs/view/4459840887",
    ):
        m = web_app.LINKEDIN_JOB_VIEW_URL_RE.search(url)
        assert m is not None, url
        assert m.group(1) == "4459840887"


def test_linkedin_job_view_url_re_does_not_match_unrelated_urls():
    assert web_app.LINKEDIN_JOB_VIEW_URL_RE.search("https://www.finn.no/job/12345") is None
    assert web_app.LINKEDIN_JOB_VIEW_URL_RE.search("https://www.linkedin.com/in/someone/") is None


class _FakeResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


def test_fetch_linkedin_preview_parses_og_title(monkeypatch):
    html = (
        '<html><head><title>x</title>'
        '<meta property="og:title" content="HCLTech hiring Desktop Support Engineer '
        'in Trondheim, Trøndelag, Norway | LinkedIn">'
        "</head></html>"
    )
    monkeypatch.setattr(web_app.requests, "get", lambda *a, **kw: _FakeResponse(html))
    monkeypatch.setattr(
        web_app.jobbnorge_client, "_build_municipality_county_map",
        lambda: {"TRONDHEIM": "Trøndelag"},
    )
    result = web_app.fetch_linkedin_preview("https://www.linkedin.com/jobs/view/4459840887/")
    assert result["title"] == "Desktop Support Engineer"
    assert result["business_name"] == "HCLTech"
    assert result["municipal"] == "Trondheim"
    assert result["county"] == "Trøndelag"


def test_fetch_linkedin_preview_falls_back_when_og_title_unparseable(monkeypatch):
    """A page whose og:title doesn't match the expected "X hiring Y in Z"
    shape still returns something usable (the raw title) rather than
    raising — the user can fill in the rest by hand."""
    html = '<meta property="og:title" content="Some Odd Page Title | LinkedIn">'
    monkeypatch.setattr(web_app.requests, "get", lambda *a, **kw: _FakeResponse(html))
    result = web_app.fetch_linkedin_preview("https://www.linkedin.com/jobs/view/123/")
    assert result["title"] == "Some Odd Page Title | LinkedIn"
    assert result["business_name"] == ""


def test_fetch_linkedin_preview_raises_when_no_og_title(monkeypatch):
    monkeypatch.setattr(web_app.requests, "get", lambda *a, **kw: _FakeResponse("<html>no meta tags here</html>"))
    try:
        web_app.fetch_linkedin_preview("https://www.linkedin.com/jobs/view/123/")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_rescore_one_scores_a_single_row(tmp_path):
    import scoring

    conn = db.connect(tmp_path / "test.db")
    row = {
        "uuid": "one-off", "status": "ACTIVE", "title": "IT-support", "business_name": "Test AS",
        "municipal": "Sogndal", "county": "Vestland",
        "description": "Vi søker en IT-supportmedarbeider med erfaring fra brukerstøtte.",
        "employer_name": "Test AS", "application_url": None, "application_due": None,
        "link": None, "engagement_type": "Fast", "extent": "Heltid", "sector": None,
    }
    db.upsert_vacancy_row(conn, row, source="manual")
    assert conn.execute("SELECT score FROM vacancies WHERE uuid = 'one-off'").fetchone()["score"] is None

    result = scoring.rescore_one(conn, "one-off")
    assert result is not None
    assert result["score"] > 0
    stored = conn.execute("SELECT score, score_it FROM vacancies WHERE uuid = 'one-off'").fetchone()
    assert stored["score"] == result["score"]
    assert stored["score_it"] is not None


def test_rescore_one_returns_none_for_missing_uuid(tmp_path):
    import scoring

    conn = db.connect(tmp_path / "test.db")
    assert scoring.rescore_one(conn, "does-not-exist") is None
