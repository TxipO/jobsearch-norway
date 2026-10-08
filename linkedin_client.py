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
from gmail_client import digest_query, fetch_plain_texts
from jobbnorge_client import _build_municipality_county_map

BLOCK_SPLIT_RE = re.compile(r"-{20,}")
# www. in practice, but the same email also linked the profile page via a
# locale subdomain (no.linkedin.com) — tolerate one here too rather than
# assume every job link is always www.
VIEW_JOB_RE = re.compile(r"https://(?:www|[a-z]{2})\.linkedin\.com/comm/jobs/view/(\d+)")

# Lines in a digest that are NOT part of a job card: the alert header, the
# "N jobs match your preferences" line, and the section headers of a
# multi-alert digest ("See all jobs on LinkedIn: <url>", "New jobs from your
# other alerts", "<strong>Q</strong> jobs in Norway", "Edit alert <url>").
# Anything carrying a URL or an HTML tag is also never title/employer/location.
_NON_CARD_LINE_RE = re.compile(
    r"https?://|<[^>]+>"
    r"|^(?:Your job alert for\b|New jobs from your other alerts|See all jobs\b|Edit alert\b"
    r"|Manage (?:your )?(?:job )?alerts?\b)"
    r"|\bjobs?\s+match(?:es)?\s+your\s+preferences",
    re.I,
)
_REGION_WORDS_RE = re.compile(r"^Greater\s+|\s+(?:Metropolitan\s+)?(?:Region|Area)$", re.I)


# Shorter than the shared 60-day window on purpose. A LinkedIn digest carries no
# deadline and no closing signal, and a job shows up in the digests for a median
# of ONE day (p90 20 days, measured on 309 jobs, 2026-10-08) — so a card from a
# mail 40 days old is almost certainly a closed job, yet a row's first_seen_at
# starts the moment we insert it. With the 60-day window, the first sync after
# the 2026-10-08 parser fix would have inserted 107 jobs last seen 31-60 days
# ago as "new". 21 days still covers a three-week gap between syncs.
LINKEDIN_LOOKBACK_DAYS = 21


def fetch_digest_texts() -> list[str]:
    """One text body per LinkedIn job-alert email in the last
    LINKEDIN_LOOKBACK_DAYS days."""
    return fetch_plain_texts(digest_query("from:jobalerts-noreply@linkedin.com", LINKEDIN_LOOKBACK_DAYS))


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
    """Split on the dashed rules between job cards; within each block, drop
    the non-card lines (alert header, section headers — see _NON_CARD_LINE_RE)
    and read what is left before "View job: {url}" as title, employer,
    location, then optional badge lines ("This company is actively hiring",
    "Apply with resume & profile", "N connections", "N company alum").

    Reads from the FRONT of the card, not back from a ", Norway" suffix: the
    original anchor required the location line to end in ", Norway", but
    LinkedIn writes most places bare ("Bergen", "Stavanger/Sandnes", "Greater
    Oslo Region", "Lindesnes") and only some as "Oslo, Norway" — so 157 of 201
    digest mails parsed to nothing and 276 of 309 distinct jobs in the last 60
    days never reached the DB (found 2026-10-08). Checked against all 201 live
    digests: every "View job:" anchor yields an entry (756/756), and no title,
    employer or location carries a URL or HTML tag."""
    entries = []
    for block in BLOCK_SPLIT_RE.split(text):
        lines = [l.strip() for l in block.splitlines() if l.strip()]
        view_idx = next((i for i, l in enumerate(lines) if l.startswith("View job:")), None)
        if view_idx is None:
            continue
        m = VIEW_JOB_RE.search(lines[view_idx])
        if not m:
            continue
        card = [l for l in lines[:view_idx] if not _NON_CARD_LINE_RE.search(l)]
        if len(card) < 3:
            continue
        title, employer, location = card[:3]
        entries.append({
            "job_id": m.group(1),
            "title": _strip_employer_suffix(title, employer),
            "employer": employer,
            "location": location,
        })
    return entries


def _resolve_location(location: str, municipality_county: dict[str, str]) -> tuple[str | None, str | None]:
    """LinkedIn location line -> (municipal, county). Forms seen in 201 live
    digests: "Oslo", "Oslo, Norway", "Drammen, Viken, Norway", "Greater Oslo
    Region", "Trondheim Region", "Stavanger/Sandnes" (two adjacent places),
    "Vestland, Norway" (a county, not a municipality) and a bare "Norway"
    (nationwide — no usable location)."""
    place = re.sub(r",\s*Norway$", "", location.strip(), flags=re.I)
    if place.lower() == "norway":
        return None, None
    place = _REGION_WORDS_RE.sub("", place.split(",")[0]).strip()
    parts = [p.strip() for p in place.split("/") if p.strip()] or [place]
    for part in parts:
        county = municipality_county.get(part.upper())
        if county:
            return parts[0], county
    counties = {c.upper(): c for c in municipality_county.values()}
    return parts[0], counties.get(parts[0].upper())


def to_vacancy_row(entry: dict, municipality_county: dict[str, str]) -> dict:
    municipal, county = _resolve_location(entry["location"], municipality_county)
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
    texts = fetch_digest_texts()
    for text in texts:
        entries.extend(parse_digest(text))

    municipality_county = _build_municipality_county_map()

    seen = set()
    upserted = 0
    for entry in entries:
        row = to_vacancy_row(entry, municipality_county)
        if row["uuid"] in seen:
            continue
        seen.add(row["uuid"])
        # upsert_vacancy_row returns False for a tombstoned (trashed +
        # deleted) uuid — nothing written, so it must not count (review
        # 2026-10-05).
        if upsert_vacancy_row(conn, row, source="linkedin"):
            upserted += 1

    # Count the cards independently of the parser, so cards it cannot read show
    # up as a gap instead of vanishing. The old "0 entries parsed" warning (digest
    # drift used to be silent — fullreview Stage 2 item 12, 2026-10-05) never
    # fired for the 2026-10-08 incident: 78% of the mails parsed to nothing for
    # weeks, but a few old-format mails still parsed, so the total was never 0.
    cards = sum(len(re.findall(r"^\s*View job:", t, re.M)) for t in texts)
    stats = {"messages": len(texts), "cards": cards, "parsed": len(entries), "upserted": upserted}
    if texts and not cards:
        stats["warning"] = (
            f"{len(texts)} LinkedIn digest message(s) fetched but no job cards found in them — "
            f"the digest format may have changed; check parse_digest()."
        )
    elif len(entries) < cards:
        stats["warning"] = (
            f"{cards - len(entries)} of {cards} LinkedIn job card(s) could not be parsed — "
            f"the digest format may have changed; check parse_digest()."
        )
    return stats
