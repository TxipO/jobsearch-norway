"""Sogndal kommune's EasyCruit-hosted job listings.

Deliberately NOT a nationwide "4th source": EasyCruit is per-tenant
subdomain (like Webcruiter), no central index across employers. This one
tenant is worth it on its own merits: Sogndal kommune is the user's #1
priority location, and (measured 2026-07-19) none of its listings overlap
with NAV/Jobbnorge.

The HTML list page (https://sogndal.easycruit.com/) sits behind an AWS WAF
JS challenge, but the official XML export (/export/xml/vacancy/list.xml) is
not (found 2026-11-13) and lists every current vacancy, so sync() reads the
ids from it each run. Detail pages are WAF-free plain HTML.
"""

import html as html_lib
import json
import re
import sqlite3
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests

from db import get_state, set_state, upsert_vacancy_row

BASE_URL = "https://sogndal.easycruit.com"
KNOWN_IDS_KEY = "easycruit_sogndal_known_ids"
IDS_REFRESHED_AT_KEY = "easycruit_sogndal_ids_refreshed_at"
LIST_URL = f"{BASE_URL}/export/xml/vacancy/list.xml"
_NS = {"ec": "urn:EasyCruit"}

_TITLE_RE = re.compile(r'<meta property="og:title" content="([^"]*)"')
_DESCRIPTION_RE = re.compile(r'<div class="jd-description">(.*?)</div>\s*<div class="bottom-buttons">', re.DOTALL)
_LOCATION_RE = re.compile(r'<div class="jd-location">\s*<h3>[^<]*</h3>\s*<p>([^<]*)</p>', re.DOTALL)
_COUNTY_RE = re.compile(r'<div class="jd-counties">.*?<li>([^<]*)</li>', re.DOTALL)
_DEADLINE_RE = re.compile(r'<div class="jd-deadline">\s*<h3>[^<]*</h3>\s*<p>([^<]*)</p>', re.DOTALL)
_WORKHOURS_RE = re.compile(r'<div class="jd-workhours">\s*<h3>[^<]*</h3>\s*<p>([^<]*)</p>', re.DOTALL)
_TYPE_RE = re.compile(r'<div class="jd-type">\s*<h3>[^<]*</h3>\s*<p>([^<]*)</p>', re.DOTALL)


def set_known_ids(conn: sqlite3.Connection, ids: list[tuple[str, str]]) -> None:
    """Store the last list read from list.xml (fallback if the next read fails)."""
    set_state(conn, KNOWN_IDS_KEY, json.dumps(ids))
    set_state(conn, IDS_REFRESHED_AT_KEY, datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))


def fetch_vacancy_ids() -> list[tuple[str, str]]:
    """(vacancy_id, first department_id) for every vacancy in the XML export."""
    resp = requests.get(LIST_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
    resp.raise_for_status()
    ids = []
    for vac in ET.fromstring(resp.content).findall("ec:Vacancy", _NS):
        dep = vac.find("ec:Departments/ec:Department", _NS)
        if dep is not None and vac.get("id") and dep.get("id"):
            ids.append((vac.get("id"), dep.get("id")))
    return ids


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


def _text(m: re.Match | None) -> str | None:
    """Captured attribute/element text -> plain text. og:title and the
    location/county/type fields arrive HTML-entity-encoded ("Rådgiver &amp;
    saksbehandler"); the UI escapes again, so storing them raw showed a
    literal "&amp;" (2026-10-05 audit; same shape as the LinkedIn fix in
    b1430d1). description stays raw HTML on purpose — it's sanitized for
    display, not shown as text."""
    return html_lib.unescape(m.group(1)).strip() if m else None


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
        "title": _text(title_m),
        "description": description_m.group(1).strip(),
        "municipal": _text(location_m) or "Sogndal",
        "county": _text(county_m) or "Vestland",
        "application_due": _to_iso_date(deadline_m.group(1) if deadline_m else None),
        "engagement_type": _text(type_m),
        "extent": _text(workhours_m),
        "business_name": "Sogndal kommune",
        "employer_name": "Sogndal kommune",
        "application_url": f"{BASE_URL}/vacancy/application/{vacancy_id}/{department_id}?iso=nn",
        "link": f"{BASE_URL}/vacancy/{vacancy_id}/{department_id}?iso=nn",
        "sector": "Offentlig",
    }


def sync(conn: sqlite3.Connection) -> dict:
    warnings = []
    try:
        known_ids = fetch_vacancy_ids()
        set_known_ids(conn, known_ids)
    except (requests.RequestException, ET.ParseError):
        known_ids = get_known_ids(conn)
        warnings.append("the automatic vacancy list (list.xml) could not be read; using the last stored list")
    fetched, failed = 0, 0
    for vacancy_id, department_id in known_ids:
        try:
            row = fetch_vacancy_detail(vacancy_id, department_id)
        except (requests.RequestException, UnicodeDecodeError):
            # UnicodeDecodeError isn't a RequestException subclass; one
            # malformed response must not abort sync() (sync.py has no outer guard).
            failed += 1
            continue
        if row is None:
            failed += 1
            continue
        # False = tombstoned uuid, nothing written: not counted (review 2026-10-05).
        if upsert_vacancy_row(conn, row, source="easycruit"):
            fetched += 1
    stats = {"known": len(known_ids), "fetched": fetched, "failed": failed}
    if failed:
        # The list is current, so a failure is a real problem, not a closed vacancy.
        warnings.append(f"{failed} of {len(known_ids)} listed vacancies could not be fetched (page changed or site error)")
    if warnings:
        stats["warning"] = "; ".join(warnings)
    return stats
