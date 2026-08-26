"""Parses finn.no "saved search" digest emails via Gmail — the only legal
path to finn.no data (see jobsearch-norway-sources memory: finn.no's
robots.txt explicitly forbids crawling; this never touches finn.no's own
servers, only the user's own mailbox).

Real digest structure (verified 2026-07-17, first live email): the
plain-text MIME part lists entries separated by a line of 20+ dashes. Each
entry's last two non-empty lines before "Flere detaljer: {url}" are
employer+location and title, in that order. No full description is
available in the digest — only title/employer/location/link, same shape as
a bare NAV feed entry before its detail fetch. There's no finn.no detail
endpoint we're allowed to hit (unlike Jobbnorge's undocumented-but-permitted
one — finn.no's robots.txt draws a hard line), so description stays empty
and scoring runs on title alone for this source.

County resolution reuses jobbnorge_client's municipality→county map (found
2026-07-18: this file hardcoded county=None instead, so scoring.py's
Vestland fylke bonus (+7) never applied here even when the location clearly
was in Vestland — 50 of 51 active finn.no rows had no county at all). The
map itself is general-purpose (place name -> fylke), not Jobbnorge-specific,
so building it twice per sync (once here, once there) is the only real cost
— cheap, a handful of small HTTP calls, not worth threading through sync.py
just to share one dict across two independent source syncs.
"""

import re
import sqlite3

from db import upsert_vacancy_row
from gmail_client import fetch_plain_texts
from jobbnorge_client import _build_municipality_county_map

DASH_SPLIT_RE = re.compile(r"-{20,}")
URL_RE = re.compile(r"(https://www\.finn\.no/\d+)")


def fetch_digest_texts() -> list[str]:
    """One text body per finn.no digest email currently in the mailbox."""
    return fetch_plain_texts("from:finn.no")


def parse_digest(text: str) -> list[dict]:
    """Splits on the dashed divider between entries; within each block, the
    two non-empty lines immediately before the "Flere detaljer:" line are
    (title, employer_location) — everything before that in the block is
    greeting/preamble or the stray "," left over from the previous divider,
    and is ignored."""
    entries = []
    for block in DASH_SPLIT_RE.split(text):
        lines = [l.strip() for l in block.split("\n") if l.strip()]
        url_idx = next((i for i, l in enumerate(lines) if l.startswith("Flere detaljer:")), None)
        if url_idx is None or url_idx < 2:
            continue
        url_match = URL_RE.search(lines[url_idx])
        if not url_match:
            continue

        employer_loc = lines[url_idx - 1]
        title = lines[url_idx - 2]
        if title == ",":  # leftover separator from the previous entry's divider
            if url_idx < 3:
                continue
            title = lines[url_idx - 3]
        title = title.lstrip(",").strip()

        employer, _, location = employer_loc.rpartition(",")
        entries.append({
            "title": title,
            "employer": employer.strip() or employer_loc.strip(),
            "location": location.strip(),
            "url": url_match.group(1),
        })
    return entries


def to_vacancy_row(entry: dict, municipality_county: dict[str, str]) -> dict:
    job_id = entry["url"].rsplit("/", 1)[-1]
    county = municipality_county.get(entry["location"].strip().upper())
    return {
        "uuid": f"finn-{job_id}",
        "status": "ACTIVE",
        "title": entry["title"],
        "business_name": entry["employer"],
        "employer_name": entry["employer"],
        "municipal": entry["location"],
        "county": county,
        "description": None,
        "application_url": entry["url"],
        "application_due": None,
        "link": entry["url"],
        "engagement_type": None,
        "extent": None,
        "sector": None,
    }


def sync(conn: sqlite3.Connection) -> dict:
    entries = []
    for text in fetch_digest_texts():
        entries.extend(parse_digest(text))

    municipality_county = _build_municipality_county_map()

    seen = set()
    upserted = 0
    for entry in entries:
        row = to_vacancy_row(entry, municipality_county)
        if row["uuid"] in seen:
            continue
        seen.add(row["uuid"])
        upsert_vacancy_row(conn, row, source="finn")
        upserted += 1

    return {"parsed": len(entries), "upserted": upserted}
