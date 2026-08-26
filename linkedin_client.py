"""Parses LinkedIn job-alert digest emails via Gmail — the only legal path
to LinkedIn data. LinkedIn's ToS bans automated/scripted access, and 2026
detection flags an account within 48h with first-offense suspension (see
jobsearch-linkedin memory); the account that would be flagged is the user's
own real profile, actively used for job search — not worth it for any
amount of extra description text. This never touches linkedin.com's own
servers, only the user's mailbox, via job alerts (up to 20, configured
2026-08-09) forwarded jobalerts-noreply@linkedin.com -> the address
gmail_client.py reads, by a Gmail filter set up on the account LinkedIn
actually emails (its registered primary address, a different inbox from
the one gmail_client.py reads).

Real digest structure (verified 2026-08-10 against two live auto-forwarded
digest emails — the SMTP-level Gmail filter forward, not a manual Forward
click, which re-renders the body differently, see jobsearch-linkedin
memory): each job is one dash-separated block of plain text: title line,
employer line, location line ("{kommune}, {county}, Norway" or
"{kommune}, Norway" with no county), optional badge line(s) like "N company
alum" or "This company is actively hiring", then a "View job: {url}" line
pointing at https://www.linkedin.com/comm/jobs/view/{id} (other query
params are single-use tracking tokens, dropped). No description text
appears anywhere in the email, same shape as finn.no's digest — see
scoring._build_description_lender_lookup() for the shared
borrow-from-NAV/Jobbnorge fallback both sources rely on.
"""

import re
import sqlite3

from db import upsert_vacancy_row
from gmail_client import fetch_plain_texts
from jobbnorge_client import _build_municipality_county_map

BLOCK_SPLIT_RE = re.compile(r"-{20,}")
# www. in practice, but the same email also linked the profile page via a
# locale subdomain (no.linkedin.com) — tolerate one here too rather than
# assume every job link is always www.
VIEW_JOB_RE = re.compile(r"https://(?:www|[a-z]{2})\.linkedin\.com/comm/jobs/view/(\d+)")


def fetch_digest_texts() -> list[str]:
    """One text body per LinkedIn job-alert email currently in the mailbox."""
    return fetch_plain_texts("from:jobalerts-noreply@linkedin.com")


def _strip_employer_suffix(title: str, employer: str) -> str:
    """Some real digest titles embed the whole card as one line: "{real
    title} - {employer} - Søknadsfrist: {date}" (live case, 2026-08-10:
    Politiets IT-enhet). That's LinkedIn's own headline text, not a parsing
    bug, but it duplicates the employer (already its own field) and breaks
    _dedup_key() matching against the same job's NAV/Jobbnorge copy, whose
    title has no such suffix. Strip from " - {employer}" onward when present;
    leave untouched otherwise (a title coincidentally containing " - " plus
    unrelated text stays as-is — no suffix to cut)."""
    suffix_start = title.find(f" - {employer}")
    return title[:suffix_start].rstrip() if suffix_start != -1 else title


def parse_digest(text: str) -> list[dict]:
    """Split on the dashed rules between job cards, then within each block
    find the "View job: {url}" line and walk backward to the location line
    (ends in ", Norway" — badge lines like "N company alum" sit between
    location and "View job:" so can't be counted forward from the block
    start), employer is the line above location, title the line above that.
    Anchoring backward from "View job:" instead of forward from the block
    start avoids the intro/preamble text in the email's first block being
    mistaken for a job title. Verified against two live 2026-08-09/08-10
    auto-forwarded digest emails (5 and 6 jobs respectively, all correct)."""
    entries = []
    for block in BLOCK_SPLIT_RE.split(text):
        lines = [l.strip() for l in block.splitlines() if l.strip()]
        view_idx = next((i for i, l in enumerate(lines) if l.startswith("View job:")), None)
        if view_idx is None:
            continue
        m = VIEW_JOB_RE.search(lines[view_idx])
        if not m:
            continue
        loc_idx = next((i for i in range(view_idx - 1, -1, -1) if lines[i].endswith(", Norway")), None)
        if loc_idx is None or loc_idx < 2:
            continue
        employer = lines[loc_idx - 1]
        entries.append({
            "job_id": m.group(1),
            "title": _strip_employer_suffix(lines[loc_idx - 2], employer),
            "employer": employer,
            "location": lines[loc_idx],
        })
    return entries


def to_vacancy_row(entry: dict, municipality_county: dict[str, str]) -> dict:
    # "Oslo, Oslo, Norway" / "Drammen, Viken, Norway" / "Oslo, Norway" (no
    # county segment) all start with the kommune — take the first comma
    # segment regardless of how many follow, rather than parsing "Norway"
    # or the county name specifically (which isn't always present).
    municipal = entry["location"].split(",")[0].strip()
    county = municipality_county.get(municipal.upper())
    url = f"https://www.linkedin.com/jobs/view/{entry['job_id']}"
    return {
        "uuid": f"linkedin-{entry['job_id']}",
        "status": "ACTIVE",
        "title": entry["title"],
        "business_name": entry["employer"],
        "employer_name": entry["employer"],
        "municipal": municipal,
        "county": county,
        "description": None,
        "application_url": url,
        "application_due": None,
        "link": url,
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
        upsert_vacancy_row(conn, row, source="linkedin")
        upserted += 1

    return {"parsed": len(entries), "upserted": upserted}
