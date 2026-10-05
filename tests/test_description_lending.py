"""Regression tests for finn.no description-borrowing (rescore_all +
scoring._build_description_lender_lookup).

Measured before building (2026-07-18): finn.no's robots.txt makes fetching
a real description illegal (see finn_client.py), so finn rows only ever
score on title. Cross-referencing against NAV/Jobbnorge rows sharing the
same employer+title+municipal recovers a description for some of them —
measured live: 11 of 60 active finn rows (18%) have an exact match. An
employer+municipal-only match would have over-counted at 19/60, because a
large employer (a kommune, a chain) posts many distinct jobs at once —
matching without the title would confidently borrow the WRONG job's text."""

import db
from scoring import _build_description_lender_lookup, rescore_all

LONG_NAV_DESCRIPTION = (
    "Vi søker en engasjert medarbeider til vårt kontor i Bergen sentrum. "
    "Stillingen innebærer daglig kundekontakt, saksbehandling og oppfølging "
    "av løpende prosjekter. Gode muligheter for faglig utvikling og "
    "videreutdanning innad i organisasjonen."
)


def _make_conn(tmp_path):
    return db.connect(tmp_path / "test.db")


def _insert(conn, uuid, source, title, business_name, municipal, description=None):
    db.upsert_vacancy_row(
        conn,
        {
            "uuid": uuid, "status": "ACTIVE", "title": title,
            "business_name": business_name, "municipal": municipal,
            "description": description,
        },
        source=source,
    )


def test_lender_lookup_ignores_short_descriptions():
    """A ~90-char Jobbnorge summary (before its own full-text backfill runs)
    isn't substantial enough to lend — still just a summary, not real
    signal beyond what the title alone already gives."""
    rows = [
        {"source": "jobbnorge", "business_name": "Acme AS", "title": "Selger",
         "municipal": "Oslo", "description": "Kort sammendrag."},
    ]
    lookup = _build_description_lender_lookup(rows)
    assert lookup == {}


def test_lender_lookup_ignores_finn_and_test_sources():
    """Only NAV/Jobbnorge rows can lend — a finn row can't lend to another
    finn row (it never has a real description of its own to give)."""
    rows = [
        {"source": "finn", "business_name": "Acme AS", "title": "Selger",
         "municipal": "Oslo", "description": LONG_NAV_DESCRIPTION},
    ]
    lookup = _build_description_lender_lookup(rows)
    assert lookup == {}


def test_finn_row_borrows_matching_description(tmp_path):
    conn = _make_conn(tmp_path)
    _insert(conn, "nav-1", "nav", "Selger", "Acme AS", "Bergen", description=LONG_NAV_DESCRIPTION)
    _insert(conn, "finn-1", "finn", "Selger", "Acme AS", "Bergen", description=None)

    rescore_all(conn)

    finn_row = db.get_vacancy(conn, "finn-1")
    assert finn_row["description"] == LONG_NAV_DESCRIPTION
    assert finn_row["description_borrowed_from"] == "nav-1"
    # The lending NAV row itself must be untouched.
    assert db.get_vacancy(conn, "nav-1")["description_borrowed_from"] is None


def test_finn_row_without_a_match_stays_empty(tmp_path):
    conn = _make_conn(tmp_path)
    _insert(conn, "finn-1", "finn", "Selger", "Nowhere AS", "Alta", description=None)

    rescore_all(conn)

    finn_row = db.get_vacancy(conn, "finn-1")
    assert finn_row["description"] is None
    assert finn_row["description_borrowed_from"] is None


def test_different_job_same_employer_does_not_borrow_wrong_description(tmp_path):
    """The whole point of matching on employer+TITLE+municipal, not just
    employer+municipal: a large employer (kommune, chain) posts many
    distinct jobs at once. An employer-only match would confidently score
    a finn "Avdelingsleder" listing using a completely unrelated
    "Sykepleier" job's text."""
    conn = _make_conn(tmp_path)
    _insert(conn, "nav-1", "nav", "Sykepleier", "Bergen kommune", "Bergen", description=LONG_NAV_DESCRIPTION)
    _insert(conn, "finn-1", "finn", "Avdelingsleder", "Bergen kommune", "Bergen", description=None)

    rescore_all(conn)

    finn_row = db.get_vacancy(conn, "finn-1")
    assert finn_row["description"] is None
    assert finn_row["description_borrowed_from"] is None


def test_borrow_is_kept_when_lender_disappears(tmp_path):
    """The lending NAV row closes (goes INACTIVE, dropped from
    iter_scorable_vacancies) — the finn row KEEPS its borrowed description
    (2026-10-05). Clearing it used to drop the hard-blocks the text carried,
    so a blocked/closed job resurfaced as a fresh row once its twin expired;
    the borrower is retired by age instead (db.retire_stale_digest_rows)."""
    conn = _make_conn(tmp_path)
    _insert(conn, "nav-1", "nav", "Selger", "Acme AS", "Bergen", description=LONG_NAV_DESCRIPTION)
    _insert(conn, "finn-1", "finn", "Selger", "Acme AS", "Bergen", description=None)
    rescore_all(conn)
    assert db.get_vacancy(conn, "finn-1")["description_borrowed_from"] == "nav-1"

    db.mark_status(conn, "nav-1", "INACTIVE", "Selger", "Acme AS", "Bergen")
    rescore_all(conn)

    finn_row = db.get_vacancy(conn, "finn-1")
    assert finn_row["description"] == LONG_NAV_DESCRIPTION
    assert finn_row["description_borrowed_from"] == "nav-1"


def test_borrower_stays_excluded_after_lender_is_deleted(tmp_path):
    """Regression (2026-10-05): the lender's text carried a hard-block; the
    lender expired and was deleted (delete_inactive); the next rescore cleared
    the borrowed text and the blocked finn row reappeared as fresh."""
    conn = _make_conn(tmp_path)
    blocked = LONG_NAV_DESCRIPTION + " Stillingen krever sikkerhetsklarering på nivå hemmelig."
    _insert(conn, "nav-1", "nav", "Selger", "Acme AS", "Bergen", description=blocked)
    _insert(conn, "finn-1", "finn", "Selger", "Acme AS", "Bergen", description=None)
    rescore_all(conn)
    assert db.get_vacancy(conn, "finn-1")["excluded"] == 1

    db.mark_status(conn, "nav-1", "INACTIVE", "Selger", "Acme AS", "Bergen")
    assert db.delete_inactive(conn) == 1
    rescore_all(conn)

    finn_row = db.get_vacancy(conn, "finn-1")
    assert finn_row["description"] == blocked
    assert finn_row["excluded"] == 1


def test_borrow_switches_to_a_new_lender_when_one_matches(tmp_path):
    """Keeping a borrowed text must not freeze it: a live lender still wins."""
    conn = _make_conn(tmp_path)
    _insert(conn, "nav-1", "nav", "Selger", "Acme AS", "Bergen", description=LONG_NAV_DESCRIPTION)
    _insert(conn, "finn-1", "finn", "Selger", "Acme AS", "Bergen", description=None)
    rescore_all(conn)
    db.mark_status(conn, "nav-1", "INACTIVE", "Selger", "Acme AS", "Bergen")
    newer = LONG_NAV_DESCRIPTION + " Oppdatert annonse."
    _insert(conn, "jobbnorge-9", "jobbnorge", "Selger", "Acme AS", "Bergen", description=newer)

    rescore_all(conn)

    finn_row = db.get_vacancy(conn, "finn-1")
    assert finn_row["description"] == newer
    assert finn_row["description_borrowed_from"] == "jobbnorge-9"


def test_borrowed_description_feeds_scoring(tmp_path):
    """The actual point: a finn row with a borrowed description scores
    higher than one with no match at all, when the borrowed text contains
    real signal (here: remote work)."""
    conn = _make_conn(tmp_path)
    remote_description = LONG_NAV_DESCRIPTION + " Fullt remote, hjemmekontor hele uken."
    _insert(conn, "nav-1", "nav", "IT-konsulent", "RemoteCo AS", "Oslo", description=remote_description)
    _insert(conn, "finn-1", "finn", "IT-konsulent", "RemoteCo AS", "Oslo", description=None)
    _insert(conn, "finn-2", "finn", "IT-konsulent", "OtherCo AS", "Oslo", description=None)

    rescore_all(conn)

    borrowed_score = db.get_vacancy(conn, "finn-1")["score"]
    no_match_score = db.get_vacancy(conn, "finn-2")["score"]
    assert borrowed_score > no_match_score


def test_detail_page_shows_borrowed_notice_when_lender_is_gone(tmp_path, monkeypatch):
    """Regression (2026-10-05): the borrower keeps the lender's text after the
    lender is deleted, but the detail page hid the notice (lender lookup came
    back empty) so another ad's text looked like finn's own."""
    from starlette.requests import Request
    from web import app as web_app
    conn = _make_conn(tmp_path)
    real_connect = db.connect
    monkeypatch.setattr(web_app.db, "connect", lambda *a, **kw: real_connect(tmp_path / "test.db"))
    _insert(conn, "nav-1", "nav", "Selger", "Acme AS", "Bergen", description=LONG_NAV_DESCRIPTION)
    _insert(conn, "finn-1", "finn", "Selger", "Acme AS", "Bergen", description=None)
    rescore_all(conn)

    def page():
        req = Request({"type": "http", "method": "GET", "path": "/vacancy/finn-1", "headers": [], "query_string": b""})
        return web_app.vacancy_detail(req, "finn-1").body.decode()

    assert "borrowed-notice" in page() and "/vacancy/nav-1" in page()  # lender alive

    db.mark_status(conn, "nav-1", "INACTIVE", "Selger", "Acme AS", "Bergen")
    db.delete_inactive(conn)
    rescore_all(conn)

    html = page()
    assert "borrowed-notice" in html
    assert "яке вже закрите" in html
