"""Client for the Jobbnorge Public API (publicapi.jobbnorge.no).

Unlike pam-stilling-feed (a changelog), this is a snapshot search API — it
returns whatever's currently active, no bootstrap/cursor needed. No auth.
See jobsearch-norway-sources memory for the full investigation (county ids,
endpoint list, why we picked this over the deprecated NAV public-feed).

The documented API's JobResultV1 has no description field at all — only
`summary`, truncated to ~90-256 chars. Verified live 2026-07-17: 91% of our
combined vacancy list was jobbnorge rows scored on that ~90 chars, meaning
hard_blocks/scoring never saw fagbrev/autorisasjon/erfaring text that was
sitting one click away. fetch_full_description() below fixes that via an
undocumented endpoint the site's own SPA calls (see DETAIL_URL docstring).
"""

import concurrent.futures
import re
import sqlite3

import requests

from db import (
    mark_description_fetch_attempted,
    rows_needing_full_description,
    set_extent_percent,
    strip_html,
    update_description,
    upsert_vacancy_row,
)

BASE_URL = "https://publicapi.jobbnorge.no"
PAGE_SIZE = 100
DETAIL_WORKERS = 8

# Undocumented — this is what the Angular SPA on jobbnorge.no itself calls to
# render a job page (found via browser network inspection, not the public
# Swagger spec). Verified 2026-07-17: plain GET, no auth, no robots.txt
# restriction on this host (id.jobbnorge.no/robots.txt is a 404 — the only
# disallow on the whole jobbnorge.no property is the PDF export path, which
# this isn't). Could change without notice since it's not the documented
# API — every caller must tolerate failure and fall back to `summary`.
DETAIL_URL = "https://id.jobbnorge.no/api/joblisting"

# Norway's official postal-code registry (Posten/Bring, updated ~twice a
# year). Not documented as an API — it's the plain-text file the postal
# service itself publishes — but no auth, no robots.txt restriction, and
# it's exactly the poststed -> kommune mapping the county-lookup table is
# missing: jobbnorge's `location` field is very often a poststed name
# ("Isdalstø", "Mysen", "Hommelvik") rather than the formal municipality
# name ("Alver", "Indre Østfold", "Malvik") — plain municipality-name
# matching alone left 664/1581 rows unresolved.
POSTAL_REGISTRY_URL = "https://www.bring.no/postnummerregister-ansi.txt"

DATE_RE = re.compile(r"^(\d{2})\.(\d{2})\.(\d{4})$")

# Title percentages are trustworthy as-is — a short job title mentioning a
# number followed by "%" is, in practice, always its own extent
# ("Butikkmedarbeider 20%"), never an unrelated stat.
TITLE_PERCENT_RE = re.compile(r"(\d{1,3})\s?%")

# Description text is NOT safe to scan the same way: real postings contain
# unrelated percentages ("95% customer satisfaction", growth figures,
# discounts...). Live false positive caught before it shipped, 2026-07-17 —
# the manually-added Sector Alarm listing says "our 95% customer
# satisfaction rate", which would have overwritten its real 20-25% extent.
# Only trust a percentage found within ~20 chars of "stilling".
DESC_PERCENT_RE = re.compile(r"(\d{1,3})\s?%[^.\n]{0,20}?stilling|stilling[^.\n]{0,20}?(\d{1,3})\s?%", re.I)


def _to_iso_date(d: str | None) -> str | None:
    if not d:
        return None
    m = DATE_RE.match(d)
    if not m:
        return d
    day, month, year = m.groups()
    return f"{year}-{month}-{day}"


# Fields that hold long strings but aren't prose — image/logo URLs mainly.
# Caught live 2026-07-17: a bare jobbnorge.no/logos/... image URL was
# ending up mid-sentence in the extracted description (matched the ">40
# chars" heuristic below despite having no HTML and no real content).
_NON_TEXT_KEYS = {"filepath", "logo", "imageurl", "url", "src"}


def _extract_text_from_components(components) -> str:
    """Recursively pulls every HTML-ish text field out of the joblisting
    components tree. Tags are replaced with a single space (not stripped
    outright) so adjacent block elements don't glue words together — the
    same rule db.strip_html already follows elsewhere in this codebase."""
    texts: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k.lower() in _NON_TEXT_KEYS:
                    continue
                if isinstance(v, str) and ("<" in v or len(v) > 40):
                    texts.append(v)
                else:
                    walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(components)
    return strip_html(" ".join(texts)).strip()


def fetch_full_description(job_id) -> str | None:
    try:
        resp = requests.get(DETAIL_URL, params={"jobId": job_id, "languageId": 1}, timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError):
        return None
    text = _extract_text_from_components(data.get("components") or [])
    return text or None


def fetch_counties() -> list[dict]:
    resp = requests.get(f"{BASE_URL}/v1/County", timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_poststed_to_municipality() -> dict[str, str]:
    """poststed (uppercased) -> municipality name (uppercased), from
    Norway's official postal-code registry. One poststed can span several
    postal codes / repeat many times in the file; last write wins, which is
    fine since they all point at the same municipality in practice."""
    resp = requests.get(POSTAL_REGISTRY_URL, timeout=30)
    resp.raise_for_status()
    lookup: dict[str, str] = {}
    for line in resp.content.decode("cp1252").splitlines():
        cols = line.split("\t")
        if len(cols) >= 4:
            poststed, municipality = cols[1].strip(), cols[3].strip()
            if poststed and municipality:
                lookup[poststed.upper()] = municipality.upper()
    return lookup


def _build_municipality_county_map() -> dict[str, str]:
    """Place name (uppercased — municipality name, or a poststed that
    resolves to one) -> county name. Jobbnorge's `location` field is
    free-text, not a municipality id, so this is a best-effort match, not a
    geocoder. Good enough per PLAN.md point 4a: strictly better than the
    NULL we had for every single jobbnorge row before this pass, and better
    again after adding the postal registry — coverage went from 58% to
    see-git-log on real data 2026-07-17.

    Two distinct gaps, two distinct fixes:
    1. Many northern-Norway municipalities carry an official bilingual (or
       trilingual) Norwegian/Sami/Kven name — "Tromsø - Romsa", "Gáivuotna -
       Kåfjord - Kaivuono" — while job postings just say "Tromsø" or
       "Kåfjord". Each " - "-separated part is registered individually.
    2. `location` is very often a poststed (postal place name — "Isdalstø",
       "Mysen", "Hommelvik") rather than the formal municipality name
       ("Alver", "Indre Østfold", "Malvik"). fetch_poststed_to_municipality()
       bridges poststed -> municipality, which then resolves to county via
       the same table below."""
    municipality_county: dict[str, str] = {}
    for county in fetch_counties():
        municipalities = county.get("municipality", [])
        if not municipalities:
            # Oslo is simultaneously a kommune and a fylke (Norway's one
            # city-county), so it has no nested municipality list of its
            # own — map the county name to itself, or every Oslo posting
            # (a very common location) would silently fail to resolve.
            municipality_county[county["name"].strip().upper()] = county["name"]
        for muni in municipalities:
            for part in muni["name"].split(" - "):
                municipality_county[part.strip().upper()] = county["name"]

    lookup = dict(municipality_county)
    try:
        for poststed, municipality in fetch_poststed_to_municipality().items():
            if poststed not in lookup and municipality in municipality_county:
                lookup[poststed] = municipality_county[municipality]
    except requests.RequestException:
        pass  # postal registry is a bonus signal, not required — degrade to municipality-only matching
    return lookup


def fetch_all_jobs() -> list[dict]:
    """Nationwide, no filters — the user asked for the widest possible net;
    hard_blocks.py + scoring.py do the narrowing on our side, not the source."""
    jobs = []
    page = 1
    while True:
        resp = requests.get(
            f"{BASE_URL}/v1/Jobs",
            params={"results": PAGE_SIZE, "page": page, "language": 1},
            timeout=30,
        )
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        jobs.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        page += 1
    return jobs


def _parse_extent_percent(job_scope: str | None, title: str | None, description: str | None) -> int | None:
    # "Heiltid" is the Nynorsk spelling of "Heltid" (full-time) — jobbnorge
    # itself only ever sends Bokmål, but this function is shared (scoring.py
    # imports it for every source), and easycruit_client.py explicitly
    # requests Nynorsk pages (iso=nn), so its scraped extent field reads
    # "Heiltid" — that was silently falling through to None (code-review
    # 2026-07-19).
    if job_scope in ("Heltid", "Heiltid"):
        return 100

    m = TITLE_PERCENT_RE.search(title or "")
    if m and 0 < int(m.group(1)) <= 100:
        return int(m.group(1))

    m = DESC_PERCENT_RE.search(description or "")
    if m:
        pct = int(m.group(1) or m.group(2))
        if 0 < pct <= 100:
            return pct

    return None


def to_vacancy_row(job: dict, municipality_county: dict[str, str]) -> dict:
    location = job.get("location") or ""
    county = municipality_county.get(location.strip().upper())
    return {
        "uuid": f"jobbnorge-{job['id']}",
        "status": "ACTIVE",
        "title": job.get("title"),
        "business_name": job.get("employer"),
        "employer_name": job.get("employer"),
        "municipal": location,
        "county": county,
        "description": job.get("summary"),
        "application_url": job.get("link"),
        "application_due": _to_iso_date(job.get("deadline")),
        "link": job.get("link"),
        "engagement_type": job.get("jobDuration"),
        "extent": job.get("jobScope"),
        "sector": None,
    }


def sync(conn: sqlite3.Connection) -> dict:
    """Full resync every call — this is a snapshot API, not a changelog, so
    there's no cursor to advance. Internal-only postings (isInternal=True,
    visible only to the employer's existing staff) are dropped outright:
    they're not a real opportunity for an outside applicant, no point
    storing and scoring something inapplicable."""
    jobs = [j for j in fetch_all_jobs() if not j.get("isInternal")]
    municipality_county = _build_municipality_county_map()

    # Live bug caught 2026-07-17 ("Sync now" always taking 2-4+ minutes):
    # to_vacancy_row() always sets description to the short `summary` field,
    # and upsert_vacancy_row unconditionally overwrites description on every
    # call — so every sync silently wiped out the full text
    # backfill_full_descriptions() had already fetched, making ALL 1581+
    # rows look like they needed backfill again, every single time. Preserve
    # already-enriched descriptions across syncs instead of re-fetching them.
    existing_full = dict(conn.execute(
        "SELECT uuid, description FROM vacancies WHERE source = 'jobbnorge' AND LENGTH(description) >= 300"
    ).fetchall())

    for job in jobs:
        row = to_vacancy_row(job, municipality_county)
        if row["uuid"] in existing_full:
            row["description"] = existing_full[row["uuid"]]
        pct = _parse_extent_percent(job.get("jobScope"), row["title"], row["description"])
        upsert_vacancy_row(conn, row, source="jobbnorge")
        set_extent_percent(conn, row["uuid"], pct)

    backfilled = backfill_full_descriptions(conn)
    return {"fetched": len(jobs), "descriptions_backfilled": backfilled}


def backfill_full_descriptions(conn: sqlite3.Connection) -> int:
    """Fetches full description text for jobbnorge rows still holding the
    truncated `summary`. Network calls run concurrently (I/O-bound, same
    pattern as nav_client's detail fetch); every DB write stays on this
    thread. A single row's fetch failure just leaves it on `summary` for
    next time — never aborts the batch."""
    rows = rows_needing_full_description(conn, source="jobbnorge")
    if not rows:
        return 0

    updated = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as pool:
        job_id_for = {}
        for row in rows:
            job_id = row["uuid"].removeprefix("jobbnorge-")
            job_id_for[pool.submit(fetch_full_description, job_id)] = row["uuid"]

        for future in concurrent.futures.as_completed(job_id_for):
            uuid = job_id_for[future]
            text = future.result()
            # Marked whether this attempt succeeded or not — a permanent
            # 404 must stop being retried on every sync (see
            # rows_needing_full_description's retry_after_hours cooldown).
            mark_description_fetch_attempted(conn, uuid)
            if text:
                update_description(conn, uuid, text)
                pct = _parse_extent_percent(None, None, text)
                if pct is not None:
                    set_extent_percent(conn, uuid, pct)
                updated += 1
    conn.commit()
    return updated
