"""Sogndal kommune's EasyCruit-hosted job listings.

Deliberately NOT a nationwide "4th source": EasyCruit is per-tenant
subdomain (like Webcruiter — see jobsearch-norway-sources memory), no
central index across employers, so a general EasyCruit aggregator is the
same architectural dead end Webcruiter already was. This one tenant is
worth it on its own merits: Sogndal kommune is the user's #1 priority
location, and measured 2026-07-19, 0 of its 13 active listings overlap
with anything already in the DB via NAV/Jobbnorge.

Also deliberately NOT built like nav_client.py/jobbnorge_client.py, which
poll a list/search endpoint automatically every sync. Here the LIST page
(https://sogndal.easycruit.com/) sits behind an AWS WAF JS challenge — a
plain `requests.get()` gets back "202 Accepted" with an empty body and an
`x-amzn-waf-action: challenge` header, not the listing. Individual vacancy
DETAIL pages (once you know the id) are NOT challenged at all — 200 OK,
full HTML, plain requests. Adding a headless-browser dependency
(Playwright, ~300MB Chromium download) just to defeat one WAF challenge
for ~13 listings from a single employer wasn't worth it, so the known
vacancy-id list is refreshed by hand instead (open the list page in a
real browser, copy the current /vacancy/{id}/{department_id} links) and
stored via set_known_ids(); sync() only ever fetches the WAF-free detail
pages.
"""

import json
import re
import sqlite3
from datetime import datetime

import requests

from db import get_state, set_state, upsert_vacancy_row

BASE_URL = "https://sogndal.easycruit.com"
KNOWN_IDS_KEY = "easycruit_sogndal_known_ids"

_TITLE_RE = re.compile(r'<meta property="og:title" content="([^"]*)"')
_DESCRIPTION_RE = re.compile(r'<div class="jd-description">(.*?)</div>\s*<div class="bottom-buttons">', re.DOTALL)
_LOCATION_RE = re.compile(r'<div class="jd-location">\s*<h3>[^<]*</h3>\s*<p>([^<]*)</p>', re.DOTALL)
_COUNTY_RE = re.compile(r'<div class="jd-counties">.*?<li>([^<]*)</li>', re.DOTALL)
_DEADLINE_RE = re.compile(r'<div class="jd-deadline">\s*<h3>[^<]*</h3>\s*<p>([^<]*)</p>', re.DOTALL)
_WORKHOURS_RE = re.compile(r'<div class="jd-workhours">\s*<h3>[^<]*</h3>\s*<p>([^<]*)</p>', re.DOTALL)
_TYPE_RE = re.compile(r'<div class="jd-type">\s*<h3>[^<]*</h3>\s*<p>([^<]*)</p>', re.DOTALL)


def set_known_ids(conn: sqlite3.Connection, ids: list[tuple[str, str]]) -> None:
    """ids: list of (vacancy_id, department_id) pairs scraped by hand from
    the list page — see this module's docstring for why this can't be
    automated the same way NAV/Jobbnorge are."""
    set_state(conn, KNOWN_IDS_KEY, json.dumps(ids))


def get_known_ids(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    raw = get_state(conn, KNOWN_IDS_KEY)
    return [tuple(pair) for pair in json.loads(raw)] if raw else []


def _to_iso_date(no_date: str | None) -> str | None:
    """DD.MM.YYYY (as shown on the page) -> YYYY-MM-DD, matching every
    other source's application_due format."""
    if not no_date:
        return None
    try:
        return datetime.strptime(no_date.strip(), "%d.%m.%Y").strftime("%Y-%m-%d")
    except ValueError:
        return None


def fetch_vacancy_detail(vacancy_id: str, department_id: str) -> dict | None:
    """None on a genuinely malformed/missing page (title or description
    section absent) — callers must skip, not crash the whole sync over one
    bad id."""
    resp = requests.get(
        f"{BASE_URL}/vacancy/{vacancy_id}/{department_id}",
        params={"iso": "nn"},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=20,
    )
    resp.raise_for_status()
    # The server's Content-Type header omits a charset, so requests falls
    # back to RFC 2616's ISO-8859-1 default (per resp.encoding) — wrong,
    # the page is actually UTF-8 (confirmed live: "på" round-tripped as
    # "pÃ¥"/mojibake through resp.text). Decode the raw bytes directly
    # instead of trusting resp.encoding's guess.
    html = resp.content.decode("utf-8")

    title_m = _TITLE_RE.search(html)
    description_m = _DESCRIPTION_RE.search(html)
    if not title_m or not description_m:
        return None

    location_m = _LOCATION_RE.search(html)
    county_m = _COUNTY_RE.search(html)
    deadline_m = _DEADLINE_RE.search(html)
    workhours_m = _WORKHOURS_RE.search(html)
    type_m = _TYPE_RE.search(html)

    return {
        "uuid": f"easycruit-sogndal-{vacancy_id}",
        "status": "ACTIVE",
        "title": title_m.group(1).strip(),
        "description": description_m.group(1).strip(),
        "municipal": location_m.group(1).strip() if location_m else "Sogndal",
        "county": county_m.group(1).strip() if county_m else "Vestland",
        "application_due": _to_iso_date(deadline_m.group(1) if deadline_m else None),
        "engagement_type": type_m.group(1).strip() if type_m else None,
        "extent": workhours_m.group(1).strip() if workhours_m else None,
        "business_name": "Sogndal kommune",
        "employer_name": "Sogndal kommune",
        "application_url": f"{BASE_URL}/vacancy/application/{vacancy_id}/{department_id}?iso=nn",
        "link": f"{BASE_URL}/vacancy/{vacancy_id}/{department_id}?iso=nn",
        "sector": "Offentlig",
    }


def sync(conn: sqlite3.Connection) -> dict:
    known_ids = get_known_ids(conn)
    fetched, failed = 0, 0
    for vacancy_id, department_id in known_ids:
        try:
            row = fetch_vacancy_detail(vacancy_id, department_id)
        except (requests.RequestException, UnicodeDecodeError):
            # UnicodeDecodeError isn't a RequestException subclass — without
            # catching it here too, one malformed/truncated response would
            # propagate uncaught out of sync(), and sync.py's CLI entry
            # point has no outer guard around this call at all, unlike
            # web/app.py's /sync route.
            failed += 1
            continue
        if row is None:
            failed += 1
            continue
        upsert_vacancy_row(conn, row, source="easycruit")
        fetched += 1
    return {"known": len(known_ids), "fetched": fetched, "failed": failed}
