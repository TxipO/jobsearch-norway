"""Regression tests for the manual "Додати вакансію" add-form (2026-08-30,
user-requested — "чи треба через тебе це робити кожен раз?").

Calls route functions directly (same pattern as test_web_index_filters.py)
rather than going through fastapi.testclient — no new dependency needed to
check plain function behavior.
"""

import db
import pytest
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


def test_readd_tracked_linkedin_link_with_default_status_keeps_existing_status(tmp_path, monkeypatch):
    """fullreview deep 2026-10-05: the form's default user_status="new" used
    to be applied unconditionally, so re-pasting a link for a row already
    marked 'applied' silently reverted it to 'new'."""
    conn = _setup(tmp_path, monkeypatch)
    kwargs = dict(
        link="https://www.linkedin.com/jobs/view/2222222222/", title="Role", business_name="X AS",
        municipal="Oslo", county="Oslo", description="", application_due="",
    )
    web_app.add_vacancy_submit(user_status="new", **kwargs)
    conn.execute("UPDATE vacancies SET user_status = 'applied' WHERE uuid = 'linkedin-2222222222'")
    conn.commit()
    web_app.add_vacancy_submit(user_status="new", **kwargs)
    row = conn.execute("SELECT user_status FROM vacancies WHERE uuid = 'linkedin-2222222222'").fetchone()
    assert row["user_status"] == "applied"
    # An explicit non-default pick on re-add still wins.
    web_app.add_vacancy_submit(user_status="interesting", **kwargs)
    row = conn.execute("SELECT user_status FROM vacancies WHERE uuid = 'linkedin-2222222222'").fetchone()
    assert row["user_status"] == "interesting"


def test_fetch_linkedin_preview_rejects_non_linkedin_and_non_https_urls(monkeypatch):
    """SSRF guard (2026-10-05): nothing may be fetched for these."""
    calls = []
    monkeypatch.setattr(web_app.requests, "get", lambda *a, **kw: calls.append(a) or _FakeResponse(""))
    for url in (
        "http://127.0.0.1:8000/", "https://evil.example/", "https://linkedin.com.evil.example/",
        "https://evil-linkedin.com/", "http://www.linkedin.com/jobs/view/1/",
        "https://www.linkedin.com@evil.example/", "file:///etc/passwd",
    ):
        with pytest.raises(ValueError):
            web_app.fetch_linkedin_preview(url)
    assert calls == []


def test_fetch_linkedin_preview_revalidates_redirect_target(monkeypatch):
    class Redirect:
        is_redirect = True
        headers = {"Location": "http://169.254.169.254/latest/meta-data/"}
        text = ""

        def raise_for_status(self):
            pass

    calls = []
    monkeypatch.setattr(web_app.requests, "get", lambda url, **kw: calls.append((url, kw)) or Redirect())
    with pytest.raises(ValueError):
        web_app.fetch_linkedin_preview("https://www.linkedin.com/jobs/view/1/")
    assert len(calls) == 1
    assert calls[0][1]["allow_redirects"] is False


def test_manual_readd_of_trashed_linkedin_link_brings_row_back(tmp_path, monkeypatch):
    """db.delete_archived tombstones trashed uuids so syncs don't resurrect
    them; a deliberate manual re-add must override that (2026-10-05) —
    otherwise the upsert is skipped and the redirect target 404s."""
    conn = _setup(tmp_path, monkeypatch)
    kwargs = dict(
        link="https://www.linkedin.com/jobs/view/3333333333/", title="Role", business_name="X AS",
        municipal="Oslo", county="Oslo", description="", application_due="",
    )
    web_app.add_vacancy_submit(user_status="archived", **kwargs)
    assert db.delete_archived(conn) == 1
    assert conn.execute("SELECT 1 FROM vacancies WHERE uuid = 'linkedin-3333333333'").fetchone() is None
    web_app.add_vacancy_submit(user_status="new", **kwargs)
    row = conn.execute("SELECT user_status FROM vacancies WHERE uuid = 'linkedin-3333333333'").fetchone()
    assert row is not None
    assert row["user_status"] == "new"


def test_add_vacancy_status_propagates_to_cross_source_twin(tmp_path, monkeypatch):
    """Review 2026-10-05: the add form used plain db.set_user_status, so a
    status chosen there never reached the NAV copy of the same posting."""
    conn = _setup(tmp_path, monkeypatch)
    db.upsert_vacancy_row(
        conn,
        {"uuid": "nav-1", "status": "ACTIVE", "title": "Desktop Support Engineer",
         "business_name": "HCLTech", "municipal": "Trondheim", "description": "En lang nok beskrivelse."},
        source="nav",
    )
    web_app.add_vacancy_submit(
        link="https://www.linkedin.com/jobs/view/4459840887/",
        title="Desktop Support Engineer", business_name="HCLTech", municipal="Trondheim",
        county="Trøndelag", description="", application_due="", user_status="applied",
    )
    assert db.get_vacancy(conn, "linkedin-4459840887")["user_status"] == "applied"
    assert db.get_vacancy(conn, "nav-1")["user_status"] == "applied"


def test_linkedin_url_check_rejects_parser_differential_bypasses(monkeypatch):
    """urlparse reads host linkedin.com from "https://evil.com\\@www.linkedin.com/x"
    but requests connects to evil.com. Nothing may be fetched for these."""
    calls = []
    monkeypatch.setattr(web_app.requests, "get", lambda *a, **kw: calls.append(a) or _FakeResponse(""))
    for url in (
        "https://evil.com\\@www.linkedin.com/x",
        "https://evil.com\\.linkedin.com/x",
        "https://linkedin.com@evil.com/",
        "https://user@www.linkedin.com/jobs/view/1/",
        "https://www.linkedin.com:pw@evil.com/",
        "https://linkedin.com.evil.com/",
        "https://www.linkedin.com:8443/jobs/view/1/",
        "https://www.linkedin.com:80/jobs/view/1/",
        "https://www.linkedin.com:abc/jobs/view/1/",
        "https://www.linkedin.com\t/jobs/view/1/",
        "https://www.linkedin.com/jobs/view/1/\n",
        "https://www.linkedin.com /jobs/view/1/",
        "",
    ):
        with pytest.raises(ValueError):
            web_app.fetch_linkedin_preview(url)
    assert calls == []


def test_linkedin_url_check_accepts_normal_urls():
    for url in (
        "https://www.linkedin.com/jobs/view/4459840887/",
        "https://LinkedIn.com/jobs/view/1",
        "https://no.linkedin.com/jobs/view/1/?trk=x",
        "https://www.linkedin.com:443/jobs/view/1/",
    ):
        assert web_app._is_linkedin_https_url(url), url


# --- Edit form (✎ Редагувати on the detail page) ---

ENGLISH_TEXT = (
    "We are looking for a motivated warehouse employee to join our team in Bergen. "
    "The position includes picking, packing and shipping of goods every day."
)


def _req(method, path):
    return Request({"type": "http", "method": method, "path": path, "headers": [], "query_string": b""})


def _seed(conn):
    """A finn-like row: no description, one stale borrow marker."""
    web_app.add_vacancy_submit(
        link="", title="Lagerarbeider", business_name="Acme AS", municipal="Bergen", county="Vestland",
        description="", application_due="", user_status="interesting",
    )
    uuid = conn.execute("SELECT uuid FROM vacancies").fetchone()["uuid"]
    conn.execute("UPDATE vacancies SET description_borrowed_from = 'nav-1' WHERE uuid = ?", (uuid,))
    conn.commit()
    return uuid


def _edit(uuid, **overrides):
    kwargs = dict(link="", title="Lagerarbeider", business_name="Acme AS", municipal="Bergen",
                  county="Vestland", description="", application_due="")
    kwargs.update(overrides)
    return web_app.edit_vacancy_submit(_req("POST", f"/vacancy/{uuid}/edit"), uuid, **kwargs)


def test_edit_form_is_prefilled_without_status_or_linkedin_button(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    uuid = _seed(conn)
    html = web_app.edit_vacancy_form(_req("GET", f"/vacancy/{uuid}/edit"), uuid).body.decode()
    assert 'value="Lagerarbeider"' in html and 'value="Acme AS"' in html and 'value="Vestland"' in html
    assert f'action="/vacancy/{uuid}/edit"' in html
    assert "f-status" not in html
    assert "linkedin-preview" not in html


def test_edit_form_404_for_unknown_uuid(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    with pytest.raises(Exception) as exc:
        web_app.edit_vacancy_form(_req("GET", "/vacancy/nope/edit"), "nope")
    assert getattr(exc.value, "status_code", None) == 404


def test_add_form_still_has_status_select_and_linkedin_button(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    html = web_app.add_vacancy_form(_req("GET", "/vacancy/add")).body.decode()
    assert "f-status" in html
    assert "linkedin-preview" in html


def test_edit_submit_updates_same_row_and_owns_description(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    uuid = _seed(conn)
    resp = _edit(uuid, description=ENGLISH_TEXT.replace(". ", ".\r\n"), link="https://www.finn.no/job/1",
                 application_due="30.09.2026")
    assert resp.status_code == 303 and resp.headers["location"] == f"/vacancy/{uuid}"
    assert conn.execute("SELECT COUNT(*) FROM vacancies").fetchone()[0] == 1
    row = db.get_vacancy(conn, uuid)
    assert row["description"].startswith("We are looking") and "\r" not in row["description"]
    assert row["language"] == "en"
    assert row["description_borrowed_from"] is None
    assert row["application_url"] == row["link"] == "https://www.finn.no/job/1"
    assert row["application_due_sort"] == db.normalize_due_date("30.09.2026")
    assert row["employer_name"] == "Acme AS"
    # Untouched by an edit.
    assert row["source"] == "manual" and row["user_status"] == "interesting"
    assert row["score"] is not None


def test_edit_submit_unchanged_description_keeps_borrow_and_empty_fields_become_null(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    uuid = _seed(conn)
    _edit(uuid, county="", municipal="  ", application_due="")
    row = db.get_vacancy(conn, uuid)
    assert row["county"] is None and row["municipal"] is None and row["application_due"] is None
    assert row["description_borrowed_from"] == "nav-1"  # description not edited -> borrow stays
    # Clearing a previously-set description stores NULL and lets borrowing resume.
    _edit(uuid, description=ENGLISH_TEXT)
    _edit(uuid, description="")
    row = db.get_vacancy(conn, uuid)
    assert row["description"] is None and row["description_borrowed_from"] is None
    assert row["language"] is None


def test_edit_submit_blank_title_is_rejected_without_writing(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    uuid = _seed(conn)
    resp = _edit(uuid, title="   ", county="Changed")
    assert resp.status_code == 200
    assert "backup-warning" in resp.body.decode()  # apostrophe is HTML-escaped, so match the error box
    row = db.get_vacancy(conn, uuid)
    assert row["title"] == "Lagerarbeider" and row["county"] == "Vestland"
