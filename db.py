import html as html_module
import json
import re
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from langdetect import LangDetectException, detect

DB_PATH = Path(__file__).parent / "data" / "jobsearch.db"

# user_status values: new, interesting, applied, interview, offer, rejected, ignored, archived
# "offer" added 2026-07-21 (user-requested): a distinct outcome from
# "interview" — passed the interview and received an offer — instead of
# forcing it into "interview" (still pending) or "archived".
# "ignored" added 2026-07-27 (user-requested): the employer went silent —
# no rejection, no response at all — a different outcome from "rejected"
# (an explicit no), placed right after it.
# "archived" repurposed 2026-08-04 (user-requested) from "done, no outcome
# tracked" to an explicit trash mark — labeled "Смітник" in the UI. Every
# OTHER status here means "keep this row forever, it's my application
# history"; this one means the opposite, "delete this row at the next
# sync" — see delete_archived(). Don't add new blanket "user_status !=
# 'new' is protected" logic anywhere without carving out this exception.
USER_STATUSES = ("new", "interesting", "applied", "interview", "offer", "rejected", "ignored", "archived")

SCHEMA = """
CREATE TABLE IF NOT EXISTS vacancies (
    uuid TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    title TEXT,
    business_name TEXT,
    municipal TEXT,
    county TEXT,
    description TEXT,
    employer_name TEXT,
    employer_orgnr TEXT,
    application_url TEXT,
    application_due TEXT,
    link TEXT,
    published TEXT,
    expires TEXT,
    updated TEXT,
    engagement_type TEXT,
    extent TEXT,
    sector TEXT,
    occupation_categories TEXT,
    raw_json TEXT,
    first_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_synced_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS feed_state (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

# Columns added after the initial schema — kept as an ALTER-based migration list
# rather than folded into SCHEMA/CREATE TABLE, since CREATE TABLE IF NOT EXISTS
# is a no-op against the already-populated table on every existing install.
MIGRATIONS = [
    ("vacancies", "user_status", "TEXT NOT NULL DEFAULT 'new'"),
    ("vacancies", "source", "TEXT NOT NULL DEFAULT 'nav'"),
    ("vacancies", "language", "TEXT"),
    ("vacancies", "score", "INTEGER"),
    ("vacancies", "score_breakdown", "TEXT"),
    # Hard exclusions: professions that legally require a Norwegian
    # authorisation/fagbrev the user cannot obtain. Kept as a flag rather than
    # deleting the row, so the filter stays auditable — a filter you can't
    # inspect is a filter you can't trust.
    ("vacancies", "excluded", "INTEGER NOT NULL DEFAULT 0"),
    ("vacancies", "exclusion_reason", "TEXT"),
    # Parsed employment percentage (0-100), NULL when it couldn't be
    # determined from title/description/extent at all — see PLAN.md point 4c.
    ("vacancies", "extent_percent", "INTEGER"),
    # When the Jobbnorge full-description fetch was last attempted (success
    # OR failure) for this row. Without this, backfill_full_descriptions()
    # retried the same permanently-404ing rows (~26% of them, likely hosted
    # via institution-specific sub-portals) on every single sync — 35s
    # wasted per click for zero benefit. NULL means "never tried yet".
    ("vacancies", "description_fetch_attempted_at", "TEXT"),
    # Salary figure parsed from description text, stored as the matched raw
    # text ("kr 680 000", "kr. 522.600-635.600") rather than normalized
    # min/max — Norwegian ads mix space/dot as thousands separators and
    # "kr" prefix vs "kroner" suffix inconsistently, not worth the false-
    # precision of pretending to normalize it. NULL when none found —
    # ~28% of active listings state a figure, most don't.
    ("vacancies", "salary_text", "TEXT"),
    # First figure in salary_text, digits only (thousand separators
    # stripped) — exists purely so the web UI can filter "salary >= N".
    # Deliberately NOT claimed to be a normalized annual/monthly min: the
    # raw text mixes both periods and range vs single-figure ads (see
    # salary_text's own comment above). It's a rough sort/filter handle,
    # not an authoritative figure.
    ("vacancies", "salary_min", "INTEGER"),
    # uuid of the NAV/Jobbnorge row a finn.no listing's description was
    # borrowed from (same employer+title+municipal, matched via
    # scoring._dedup_key) — finn.no's own robots.txt forbids fetching a
    # real description, so this is the only legal way to score finn rows on
    # more than a bare title. NULL means "no own description, no match
    # found" for finn, or simply "this isn't a borrowed description" for
    # every other source.
    ("vacancies", "description_borrowed_from", "TEXT"),
    # User-reported "this shouldn't be showing me" flag — a manual review
    # queue distinct from user_status (application progress) and excluded
    # (hard_blocks' own automatic legal/eligibility filter). Set via the
    # "🚩 Помилка" button; a timestamp (not a plain bool) so a review pass
    # can prioritize the oldest-flagged first. NULL = not flagged.
    ("vacancies", "flagged_at", "TEXT"),
    # Free-text personal note per vacancy (2026-07-21, user-requested) —
    # e.g. "sent via referral", "waiting on reply since June". A single
    # mutable field, not a timestamped log, matching how status/flagged_at
    # already work here; a full comment history is a bigger feature to
    # build only if this turns out not to be enough.
    ("vacancies", "notes", "TEXT"),
    # Second scoring profile, stored alongside the default ("warehouse")
    # score/score_breakdown columns — the "IT-support like before the
    # 2026-08-18 warehouse retarget" toggle (2026-08-27, user-requested).
    # Both profiles are computed and stored on every rescore_all() pass;
    # which one drives the visible list/sort/filter is picked at request
    # time via the "score_profile" feed_state value, see web/app.py.
    ("vacancies", "score_it", "INTEGER"),
    ("vacancies", "score_it_breakdown", "TEXT"),
    # Best-effort YYYY-MM-DD normalization of application_due for sorting/
    # expiry — NAV's own applicationDue field is stored verbatim
    # (upsert_active_vacancy) and isn't always ISO; live corpus has ISO
    # w/ time, dd-mm-yyyy, and d(d).m(m).yyyy all mixed in (see
    # web/app.py's format_due, which does the same 3-pattern parse for
    # DISPLAY only — this is the same normalization for SORTING).
    # NULL = free text ("Løpende") or unparseable, never an error.
    # Found 2026-08-27: sort=deadline only recognized bare ISO, silently
    # sorting every dd.mm.yyyy-formatted NAV row as if it had no deadline
    # at all (pushed to the very end despite being a real near-term date).
    ("vacancies", "application_due_sort", "TEXT"),
    # When user_status last became "applied" ("Відгукнувся") — drives the
    # auto-ignore-after-silence feature (user-requested 2026-08-30, see
    # auto_ignore_stale_applications). NULL for rows that were already
    # "applied" before this column existed — no way to know when, and
    # guessing would start a countdown from the wrong date. Those rows
    # still auto-ignore off application_due_sort ALONE when a deadline is
    # known (that's real recorded data, not a guess); only a row with
    # neither date known is left alone entirely.
    ("vacancies", "applied_at", "TEXT"),
]


def _apply_migrations(conn: sqlite3.Connection) -> None:
    for table, column, definition in MIGRATIONS:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    conn.commit()


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _apply_migrations(conn)
    return conn


BACKUP_DIR = Path(__file__).parent / "data" / "backups"
BACKUP_KEEP = 30  # ~1/day if run once per sync — plenty to recover a mistake from


def backup_db(db_path: Path = DB_PATH, backup_dir: Path = BACKUP_DIR, keep: int = BACKUP_KEEP) -> Path | None:
    """Timestamped copy of the whole DB file before anything risky touches
    it — added 2026-07-29 after a real incident: an unscoped browser-side
    test click (`querySelectorAll('.status-control .status-btn')` with no
    per-row scoping) fired status-change requests for every button on every
    visible vacancy row at once, scrambling `user_status` for ~50 real
    vacancies with no way to recover the prior values. There was no backup
    to restore from. Called at the start of trigger_sync() (web/app.py) —
    every "Sync now" click snapshots first, so a future mistake has
    something to roll back to. Copies the raw file rather than an
    sqlite backup API call: simplest thing that actually works for a local
    single-writer file, and the DB is never open with pending
    transactions at the point this runs (called before any connection is
    opened for the sync itself).
    """
    if not db_path.exists():
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = backup_dir / f"jobsearch_{stamp}.db"
    shutil.copyfile(db_path, dest)

    backups = sorted(backup_dir.glob("jobsearch_*.db"))
    for old in backups[:-keep]:
        old.unlink()

    return dest


# Block-level boundaries become a newline (not dropped like every other tag)
# before the rest is stripped, and entities are decoded. Both matter for
# anything that reasons about which clause a word sits in: hard_blocks.py's
# section/clause-scoped requirement checks and scoring.py's proximity
# windows. Found 2026-08-29 auditing the flagged-vacancy queue: a
# "<li>Truckførerbevis T8</li>" hard requirement sat right next to an
# unrelated SOFTENED bullet with no boundary between them once flattened —
# the softener ("er en fordel men ikke et krav") bled across into the real
# requirement. Same pass found HTML entities surviving into scored text
# ("4&#43; years" never matching a plain "4+ years" pattern) in 42% of the
# live corpus (4503/10768 active ads).
_BLOCK_TAG_RE = re.compile(r"</?(?:li|p|br|div|tr|h[1-6]|ul|ol|table)\b[^>]*>", re.I)


def strip_html(html: str) -> str:
    text = _BLOCK_TAG_RE.sub("\n", html or "")
    text = re.sub(r"<[^>]+>", " ", text)
    return html_module.unescape(text)


def detect_language(description: str) -> str | None:
    text = strip_html(description).strip()
    if len(text) < 20:
        return None
    try:
        return detect(text)
    except LangDetectException:
        return None


def get_state(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM feed_state WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO feed_state (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


def upsert_active_vacancy(conn: sqlite3.Connection, uuid: str, status: str, ad: dict) -> bool:
    """Returns True if this inserted a vacancy we had never seen, False if it
    updated one we already held. The caller needs the distinction to report an
    honest "N new / M updated" instead of one lump count that reads as "N new"
    even when the feed only re-sent ads we already had."""
    is_new = conn.execute("SELECT 1 FROM vacancies WHERE uuid = ?", (uuid,)).fetchone() is None
    employer = ad.get("employer") or {}
    work_locations = ad.get("workLocations") or [{}]
    location = work_locations[0] if work_locations else {}
    language = detect_language(ad.get("description"))
    conn.execute(
        """
        INSERT INTO vacancies (
            uuid, status, title, business_name, municipal, county, description,
            employer_name, employer_orgnr, application_url, application_due,
            application_due_sort, link,
            published, expires, updated, engagement_type, extent, sector,
            occupation_categories, raw_json, language, last_synced_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(uuid) DO UPDATE SET
            status = excluded.status,
            title = excluded.title,
            business_name = excluded.business_name,
            municipal = excluded.municipal,
            county = excluded.county,
            description = excluded.description,
            employer_name = excluded.employer_name,
            employer_orgnr = excluded.employer_orgnr,
            application_url = excluded.application_url,
            application_due = excluded.application_due,
            application_due_sort = excluded.application_due_sort,
            link = excluded.link,
            published = excluded.published,
            expires = excluded.expires,
            updated = excluded.updated,
            engagement_type = excluded.engagement_type,
            extent = excluded.extent,
            sector = excluded.sector,
            occupation_categories = excluded.occupation_categories,
            raw_json = excluded.raw_json,
            language = excluded.language,
            last_synced_at = datetime('now')
        """,
        (
            uuid,
            status,
            ad.get("title"),
            employer.get("name"),
            location.get("municipal"),
            location.get("county"),
            ad.get("description"),
            employer.get("name"),
            employer.get("orgnr"),
            ad.get("applicationUrl"),
            ad.get("applicationDue"),
            normalize_due_date(ad.get("applicationDue")),
            ad.get("link"),
            ad.get("published"),
            ad.get("expires"),
            ad.get("updated"),
            ad.get("engagementtype"),
            ad.get("extent"),
            ad.get("sector"),
            json.dumps(ad.get("occupationCategories") or []),
            json.dumps(ad),
            language,
        ),
    )
    conn.commit()
    return is_new


def upsert_vacancy_row(conn: sqlite3.Connection, row: dict, source: str) -> None:
    """Source-agnostic upsert for anything already shaped like our flat
    `vacancies` columns (see jobbnorge_client.to_vacancy_row). NAV keeps its
    own richer upsert_active_vacancy above because its source JSON is
    nested; this one is for flatter sources.

    description = COALESCE(excluded.description, description): a source
    that never has its own description (finn.no — see finn_client.py's
    docstring) always upserts description=None on every sync. Without the
    COALESCE, that would silently wipe out a borrowed description
    (scoring._dedup_key cross-ref against NAV/Jobbnorge) on the very next
    sync — same failure shape as the jobbnorge full-description-backfill
    bug fixed 2026-07-17, just for a different field-population path."""
    language = detect_language(row.get("description"))
    conn.execute(
        """
        INSERT INTO vacancies (
            uuid, status, title, business_name, municipal, county, description,
            employer_name, application_url, application_due, application_due_sort, link,
            engagement_type, extent, sector, source, language, last_synced_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(uuid) DO UPDATE SET
            status = excluded.status,
            title = excluded.title,
            business_name = excluded.business_name,
            municipal = excluded.municipal,
            county = excluded.county,
            description = COALESCE(excluded.description, description),
            employer_name = excluded.employer_name,
            application_url = excluded.application_url,
            application_due = excluded.application_due,
            application_due_sort = excluded.application_due_sort,
            link = excluded.link,
            engagement_type = excluded.engagement_type,
            extent = excluded.extent,
            sector = excluded.sector,
            language = excluded.language,
            last_synced_at = datetime('now')
        """,
        (
            row["uuid"], row.get("status", "ACTIVE"), row.get("title"),
            row.get("business_name"), row.get("municipal"), row.get("county"),
            row.get("description"), row.get("employer_name"),
            row.get("application_url"), row.get("application_due"),
            normalize_due_date(row.get("application_due")), row.get("link"),
            row.get("engagement_type"), row.get("extent"), row.get("sector"),
            source, language,
        ),
    )
    conn.commit()


def update_description(conn: sqlite3.Connection, uuid: str, description: str) -> None:
    """Swaps in a fuller description fetched after the initial upsert (e.g.
    Jobbnorge's summary -> full text backfill) without touching any other
    field. Re-detects language since the fuller text is more reliable."""
    language = detect_language(description)
    conn.execute(
        "UPDATE vacancies SET description = ?, language = ? WHERE uuid = ?",
        (description, language, uuid),
    )
    conn.commit()


def rows_needing_full_description(
    conn: sqlite3.Connection, source: str, max_len: int = 300, retry_after_hours: int = 24,
) -> list[sqlite3.Row]:
    """Jobbnorge rows still holding the truncated `summary` (~90-256 chars)
    rather than the fetched-separately full text. Excludes rows whose fetch
    was already attempted within `retry_after_hours` — without this, the
    ~26% of rows that permanently 404 on the detail endpoint (see
    jobbnorge_client docstring) got retried on every single sync, burning
    ~35s per click for zero benefit. A brand-new row (never attempted,
    description_fetch_attempted_at IS NULL) is always included."""
    return conn.execute(
        """
        SELECT uuid FROM vacancies WHERE source = ? AND status = 'ACTIVE'
        AND (description IS NULL OR LENGTH(description) < ?)
        AND (
            description_fetch_attempted_at IS NULL
            OR datetime(description_fetch_attempted_at) < datetime('now', ?)
        )
        """,
        (source, max_len, f"-{retry_after_hours} hours"),
    ).fetchall()


def mark_description_fetch_attempted(conn: sqlite3.Connection, uuid: str) -> None:
    conn.execute(
        "UPDATE vacancies SET description_fetch_attempted_at = datetime('now') WHERE uuid = ?",
        (uuid,),
    )


def set_extent_percent(conn: sqlite3.Connection, uuid: str, percent: int | None) -> None:
    conn.execute("UPDATE vacancies SET extent_percent = ? WHERE uuid = ?", (percent, uuid))
    conn.commit()


def set_borrowed_description(
    conn: sqlite3.Connection, uuid: str, description: str | None, source_uuid: str | None,
    language: str | None = None,
) -> None:
    """Sets (or clears, when both args are None) a description borrowed
    from another source's matching listing — see scoring._dedup_key and
    scoring._lend_finn_descriptions.

    `language`: pass the already-computed value when the caller has one
    (rescore_all does — code-review 2026-07-19 found it was calling
    detect_language on the identical description a second time right
    after this function already ran it internally). Left as an optional
    fallback here (auto-detected from `description` when omitted) so
    finn.no rows still get a language without every caller having to
    compute it themselves — finn.no's own digest email never states one."""
    if language is None:
        language = detect_language(description)
    conn.execute(
        "UPDATE vacancies SET description = ?, description_borrowed_from = ?, language = ? WHERE uuid = ?",
        (description, source_uuid, language, uuid),
    )
    conn.commit()


def set_salary_text(conn: sqlite3.Connection, uuid: str, salary_text: str | None, salary_min: int | None = None) -> None:
    conn.execute(
        "UPDATE vacancies SET salary_text = ?, salary_min = ? WHERE uuid = ?",
        (salary_text, salary_min, uuid),
    )
    conn.commit()


# Mirrors web/app.py's format_due() DUE_DATE_PATTERNS (same 3 shapes, ISO
# first) — that one normalizes TO dd.mm.yyyy for display, this one normalizes
# TO YYYY-MM-DD for lexicographic sort/date() comparison. Keep both in sync
# if a new raw shape from NAV/Jobbnorge/finn shows up.
_DUE_DATE_ISO_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
_DUE_DATE_DASH_RE = re.compile(r"^(\d{1,2})-(\d{1,2})-(\d{4})$")
_DUE_DATE_DOT_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$")


def normalize_due_date(value: str | None) -> str | None:
    """Best-effort YYYY-MM-DD for application_due; None when it's free text
    ("Løpende") or doesn't match any known shape — never raises."""
    if not value:
        return None
    m = _DUE_DATE_ISO_RE.match(value)
    if m:
        return f"{m[1]}-{m[2]}-{m[3]}"
    m = _DUE_DATE_DASH_RE.match(value) or _DUE_DATE_DOT_RE.match(value)
    if m:
        return f"{m[3]}-{int(m[2]):02d}-{int(m[1]):02d}"
    return None


def mark_status(conn: sqlite3.Connection, uuid: str, status: str, title: str, business_name: str, municipal: str) -> None:
    conn.execute(
        """
        INSERT INTO vacancies (uuid, status, title, business_name, municipal, last_synced_at)
        VALUES (?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(uuid) DO UPDATE SET
            status = excluded.status,
            last_synced_at = datetime('now')
        """,
        (uuid, status, title, business_name, municipal),
    )
    conn.commit()


def set_user_status(conn: sqlite3.Connection, uuid: str, user_status: str) -> None:
    if user_status not in USER_STATUSES:
        raise ValueError(f"Unknown user_status: {user_status!r}")
    # Stamps/resets applied_at every time the status becomes "applied" —
    # including re-applying after it had moved away — so the auto-ignore
    # countdown (auto_ignore_stale_applications) always starts from the
    # most recent actual application, not a stale earlier one.
    if user_status == "applied":
        conn.execute(
            "UPDATE vacancies SET user_status = ?, applied_at = datetime('now') WHERE uuid = ?",
            (user_status, uuid),
        )
    else:
        conn.execute("UPDATE vacancies SET user_status = ? WHERE uuid = ?", (user_status, uuid))
    conn.commit()


def set_score(conn: sqlite3.Connection, uuid: str, score: int, breakdown: dict) -> None:
    conn.execute(
        "UPDATE vacancies SET score = ?, score_breakdown = ? WHERE uuid = ?",
        (score, json.dumps(breakdown, ensure_ascii=False), uuid),
    )
    conn.commit()


def set_score_it(conn: sqlite3.Connection, uuid: str, score: int, breakdown: dict) -> None:
    conn.execute(
        "UPDATE vacancies SET score_it = ?, score_it_breakdown = ? WHERE uuid = ?",
        (score, json.dumps(breakdown, ensure_ascii=False), uuid),
    )
    conn.commit()


def set_exclusion(conn: sqlite3.Connection, uuid: str, excluded: bool, reason: str | None) -> None:
    conn.execute(
        "UPDATE vacancies SET excluded = ?, exclusion_reason = ? WHERE uuid = ?",
        (1 if excluded else 0, reason, uuid),
    )
    conn.commit()


def count_excluded(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM vacancies WHERE status = 'ACTIVE' AND excluded = 1"
    ).fetchone()[0]


def set_notes(conn: sqlite3.Connection, uuid: str, notes: str) -> None:
    # Empty string normalized to NULL so "no note" is a single consistent
    # state (NULL), not "" in some rows and NULL in others depending on
    # whether the textarea was ever touched.
    conn.execute("UPDATE vacancies SET notes = ? WHERE uuid = ?", (notes or None, uuid))
    conn.commit()


def set_flagged(conn: sqlite3.Connection, uuid: str, flagged: bool) -> None:
    # datetime('now') (UTC), matching first_seen_at/last_synced_at — not a
    # Python-side timestamp, so there's no local/UTC mismatch to get wrong
    # (see selfcheck check #9's live example from this same project).
    if flagged:
        conn.execute("UPDATE vacancies SET flagged_at = datetime('now') WHERE uuid = ?", (uuid,))
    else:
        conn.execute("UPDATE vacancies SET flagged_at = NULL WHERE uuid = ?", (uuid,))
    conn.commit()


def count_flagged(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM vacancies WHERE status = 'ACTIVE' AND flagged_at IS NOT NULL"
    ).fetchone()[0]


def list_flagged(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Oldest-flagged first — a manual review queue, not a display list.

    Same `status = 'ACTIVE'` scope as count_flagged() — an inactive
    vacancy's flag isn't worth reviewing (the listing itself is already
    gone; whatever data issue prompted the flag doesn't need a code fix
    for a row nobody can see anymore), and without this filter the review
    queue and the UI's "N flagged" badge would silently disagree
    (code-review 2026-07-19)."""
    return conn.execute(
        "SELECT * FROM vacancies WHERE status = 'ACTIVE' AND flagged_at IS NOT NULL ORDER BY flagged_at ASC"
    ).fetchall()


def iter_scorable_vacancies(conn: sqlite3.Connection, active_only: bool = True):
    where = "WHERE status = 'ACTIVE'" if active_only else ""
    return conn.execute(
        f"SELECT uuid, title, description, municipal, county, language, extent_percent, extent, "
        f"salary_text, salary_min, business_name, source, description_borrowed_from, user_status, "
        f"occupation_categories, engagement_type FROM vacancies {where}"
    ).fetchall()


def get_vacancy(conn: sqlite3.Connection, uuid: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM vacancies WHERE uuid = ?", (uuid,)).fetchone()


# Which stored scoring profile ("warehouse" — default, or "it") a given
# request should read/sort/filter by. Maps to the actual column name here,
# validated before use — a column name can't go through a `?` placeholder,
# so it lands in the SQL text itself, and an unexpected value must fail
# loudly rather than ever reach raw SQL.
SCORE_PROFILE_COLUMNS = {"warehouse": "score", "it": "score_it"}


def _score_column(profile: str) -> str:
    if profile not in SCORE_PROFILE_COLUMNS:
        raise ValueError(f"Unknown score profile: {profile!r}")
    return SCORE_PROFILE_COLUMNS[profile]


def _vacancy_filters(
    active_only: bool,
    user_status: str | list[str] | None,
    language: str | None,
    search: str | None,
    show_excluded: bool,
    source: str | None = None,
    min_score: int | None = None,
    min_salary: int | None = None,
    show_flagged: bool = False,
    min_extent_percent: int | None = None,
    occupation_category: str | None = None,
    score_profile: str = "warehouse",
) -> tuple[str, list]:
    """Shared WHERE-clause builder for list_vacancies/count_vacancies — kept
    in one place so the two can never drift out of sync with each other.

    score_profile picks which of the two stored scoring profiles
    ("warehouse" — default, or "it") min_score filters against — see
    _score_column()/SCORE_PROFILE_COLUMNS."""
    clauses = []
    params: list = []

    if active_only:
        # A vacancy the user has already reacted to beyond the untouched
        # backlog states ("new"/"interesting") is the user's own
        # application-history record, not an open listing — it must stay
        # visible in the default list, in search, and under any status
        # filter, even after the source closes it (user-requested
        # 2026-08-16). Baked into the WHERE clause itself rather than left
        # to the caller to opt out of active_only per-request: the old
        # approach required explicitly selecting the reacted status in the
        # filter panel for the exemption to kick in, so an applied-to
        # vacancy could vanish from a plain search or the unfiltered list
        # the moment NAV closed the ad — indistinguishable from the row
        # having been deleted. "interesting" stays subject to the
        # ACTIVE-only rule on purpose: it is still backlog, not a tracked
        # application.
        clauses.append("(status = 'ACTIVE' OR user_status NOT IN ('new', 'interesting'))")
    if not show_excluded:
        clauses.append("excluded = 0")
    if not show_flagged:
        clauses.append("flagged_at IS NULL")
    if user_status:
        # Accepts either a single status (str, the old call shape — kept so
        # existing callers like the kanban board don't need updating) or a
        # list (the filter panel's multi-select, 2026-08-04).
        statuses = [user_status] if isinstance(user_status, str) else user_status
        if statuses:
            clauses.append(f"user_status IN ({', '.join('?' for _ in statuses)})")
            params.extend(statuses)
    elif search:
        # User-requested 2026-09-03: a search is a deliberate lookup, not
        # passive browsing — the new/interesting-only default (below) hid a
        # vacancy the user had already marked "Відгукнувся" from its own
        # search hit. Search ignores that default and looks across every
        # status except "archived" ("Смітник"): those rows are meant to be
        # gone (delete_archived() removes them outright on the next sync;
        # this only matters in the narrow window before that runs). An
        # explicit status filter (the branch above) still wins over this —
        # ticking exactly "Відгукнувся" while searching narrows to that.
        clauses.append("user_status != 'archived'")
    else:
        # No explicit status picked AND no search — the default/unfiltered
        # main-list view — user-requested 2026-09-02: the default view is
        # the open backlog only ("new"/"interesting"), everything already
        # reacted to (applied/interview/offer/rejected/ignored/archived)
        # clutters it. Widened from an earlier rejected/ignored-only version
        # (2026-08-23) to an explicit allowlist — still one filter-panel
        # click away, not gone. Only reachable when user_status is the
        # empty/falsy default — kanban always passes an explicit single
        # status, so this never affects its columns.
        clauses.append("user_status IN ('new', 'interesting')")
    if language:
        clauses.append("language = ?")
        params.append(language)
    if source:
        clauses.append("source = ?")
        params.append(source)
    if search:
        # Space- and case-insensitive on both sides: user-reported 2026-09-03
        # — "Supermicro" (one word) didn't find "Super Micro Computer" (the
        # employer name exactly as LinkedIn wrote it) because a plain LIKE
        # substring match is literal about whitespace. SQLite's LIKE is
        # already case-insensitive for ASCII, but not diacritics (æøå) —
        # LOWER() alone doesn't fold those either, so this covers case the
        # same way for both scripts rather than relying on the ASCII-only
        # default. Stripping spaces on both sides means "super micro" and
        # "supermicro" become the same query against the same normalized
        # column, regardless of which one either side used.
        norm = "REPLACE(LOWER({col}), ' ', '')"
        clauses.append(
            "(" + " OR ".join(norm.format(col=c) + " LIKE ?" for c in ("title", "description", "business_name")) + ")"
        )
        like = f"%{search.lower().replace(' ', '')}%"
        params.extend([like, like, like])
    if min_score is not None:
        clauses.append(f"{_score_column(score_profile)} >= ?")
        params.append(min_score)
    if min_salary is not None:
        # salary_min is a rough leading figure, not a normalized
        # annual/monthly value — a real filter, but see its column comment
        # for why it's not authoritative. NULL (no stated salary) never
        # matches, which is the point of the filter.
        clauses.append("salary_min >= ?")
        params.append(min_salary)
    if min_extent_percent is not None:
        # extent_percent (0-100, employment share — full-time vs part-time)
        # is a completely different axis from `score` (our own match-quality
        # ranking) — user-requested 2026-07-25 after confusing the two via
        # the "Мін. % відповідності" field, which is score, not this.
        # NULL (couldn't be parsed from the listing) never matches.
        clauses.append("extent_percent >= ?")
        params.append(min_extent_percent)
    if occupation_category:
        # occupation_categories is a JSON array — '[{"level1": "X", ...}]',
        # written verbatim by nav_client.py (see scoring.py's
        # OCCUPATION_CATEGORY_BONUS comment). A vacancy can carry several
        # level1 tags at once, so this is a substring match, not an exact
        # column comparison — still safely parameterized (the category
        # value never enters the SQL string itself, only the bound param).
        clauses.append("occupation_categories LIKE ?")
        params.append(f'%"level1": "{occupation_category}"%')

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, params


def list_sources(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT source FROM vacancies WHERE status = 'ACTIVE' ORDER BY source"
    ).fetchall()]


def count_vacancies(
    conn: sqlite3.Connection,
    active_only: bool = True,
    user_status: str | list[str] | None = None,
    language: str | None = None,
    search: str | None = None,
    show_excluded: bool = False,
    source: str | None = None,
    min_score: int | None = None,
    min_salary: int | None = None,
    show_flagged: bool = False,
    min_extent_percent: int | None = None,
    occupation_category: str | None = None,
    score_profile: str = "warehouse",
) -> int:
    where, params = _vacancy_filters(
        active_only, user_status, language, search, show_excluded, source, min_score, min_salary, show_flagged,
        min_extent_percent, occupation_category, score_profile,
    )
    return conn.execute(f"SELECT COUNT(*) FROM vacancies {where}", params).fetchone()[0]


def list_vacancies(
    conn: sqlite3.Connection,
    active_only: bool = True,
    user_status: str | list[str] | None = None,
    language: str | None = None,
    search: str | None = None,
    show_excluded: bool = False,
    source: str | None = None,
    min_score: int | None = None,
    min_salary: int | None = None,
    sort: str = "score",
    limit: int = 50,
    offset: int = 0,
    show_flagged: bool = False,
    min_extent_percent: int | None = None,
    occupation_category: str | None = None,
    score_profile: str = "warehouse",
) -> list[sqlite3.Row]:
    where, params = _vacancy_filters(
        active_only, user_status, language, search, show_excluded, source, min_score, min_salary, show_flagged,
        min_extent_percent, occupation_category, score_profile,
    )
    params = params + [limit, offset]
    score_col = _score_column(score_profile)

    # application_due mixes real dates in several raw shapes (ISO, dd-mm-yyyy,
    # d(d).m(m).yyyy — see normalize_due_date) with free text ("Løpende",
    # "Fortløpende opptak"). application_due_sort is the precomputed
    # YYYY-MM-DD form of whichever shape it was (rescore_all populates it,
    # same pattern as extent_percent/salary_text); NULL there (free text or
    # unparseable) sorts to the very end instead of interleaving
    # lexicographically among real dates. Found 2026-08-27: sorting on the
    # raw column directly (GLOB-checking for bare ISO) silently treated
    # every non-ISO real date as if it had none at all.
    order_clause = (
        "ORDER BY COALESCE(application_due_sort, '9999-99-99') ASC"
        if sort == "deadline"
        else f"ORDER BY {score_col} DESC NULLS LAST, published DESC"
    )

    return conn.execute(
        f"""
        SELECT uuid, title, business_name, municipal, language, source,
               user_status, application_due, published, sector, {score_col} AS score,
               link, application_url, excluded, exclusion_reason, extent_percent, county,
               salary_text, first_seen_at, flagged_at, notes
        FROM vacancies
        {where}
        {order_clause}
        LIMIT ? OFFSET ?
        """,
        params,
    ).fetchall()


def count_new_high_score(conn: sqlite3.Connection, since: str, min_score: int) -> int:
    """Vacancies first seen after `since` (the previous sync's completion
    timestamp) scoring at least min_score — used for the post-sync "N new
    high-score matches" summary. Excludes hard-blocked rows, same as the
    default list view.

    `since` MUST be a UTC timestamp in the same "YYYY-MM-DD HH:MM:SS" shape
    as first_seen_at (written by SQLite's datetime('now'), which is UTC) —
    passing a local-time string here silently drops "new" rows for however
    many hours local time leads UTC by."""
    return conn.execute(
        "SELECT COUNT(*) FROM vacancies "
        "WHERE first_seen_at > ? AND score >= ? AND status = 'ACTIVE' AND excluded = 0",
        (since, min_score),
    ).fetchone()[0]


def delete_inactive(conn: sqlite3.Connection) -> int:
    """NAV's terms of use require removing listings once they're inactive,
    not just hiding them — see jobsearch-norway-sources memory. Jobbnorge
    rows never get marked INACTIVE (it's a snapshot API, gone = simply not
    in the next pull), so this only ever touches NAV-sourced rows in
    practice, but isn't restricted to NAV by source in case that changes.

    Exception: rows with user_status != 'new' are kept regardless of NAV
    status. Once the user reacts, the row stops being "a NAV listing
    republished as live" and becomes the user's own application-history
    record (same exemption delete_expired_unreacted and the kanban view's
    active_only=False already grant) — the ToU obligation is about not
    presenting dead listings as current job postings, not about erasing a
    user's own tracking of jobs they applied to. ("archived" rows are
    "kept" by this function specifically, but get removed by the separate
    delete_archived() call in the same sync — that status means the user
    wants the row gone regardless of source state.)"""
    cur = conn.execute("DELETE FROM vacancies WHERE status = 'INACTIVE' AND user_status = 'new'")
    conn.commit()
    return cur.rowcount


def delete_archived(conn: sqlite3.Connection) -> int:
    """"archived" ('Смітник' in the UI) is the one user_status that means
    the opposite of every other reacted status — the user explicitly wants
    this row gone, not preserved as application history. Unlike
    delete_inactive/delete_expired_unreacted, this ignores the source's
    ACTIVE/INACTIVE status entirely — the user's own mark is the only
    signal that matters here."""
    cur = conn.execute("DELETE FROM vacancies WHERE user_status = 'archived'")
    conn.commit()
    return cur.rowcount


def delete_expired_unreacted(conn: sqlite3.Connection) -> int:
    """Deletes vacancies whose deadline has passed and that the user never
    engaged with (still user_status='new'). Anything the user marked
    interesting/applied/interview/rejected is kept regardless of deadline
    — that's the user's own history, not clutter. ("archived" rows are
    handled separately by delete_archived — that status means "delete me",
    not "keep me".) Only acts on application_due_sort (normalize_due_date's
    precomputed YYYY-MM-DD, covers ISO/dd-mm-yyyy/d(d).m(m).yyyy alike, see
    upsert_active_vacancy/upsert_vacancy_row) — NULL there (free text like
    "we evaluate continuously", or genuinely unparseable) is left alone
    since we can't tell if it's actually expired. Before 2026-08-27 this
    GLOB-checked the raw application_due column directly, which silently
    never expired any row whose raw due date wasn't already ISO-shaped."""
    cur = conn.execute(
        """
        DELETE FROM vacancies
        WHERE user_status = 'new'
          AND application_due_sort IS NOT NULL
          AND date(application_due_sort) < date('now')
        """
    )
    conn.commit()
    return cur.rowcount


# How long a silent "applied" application stays "applied" before it's
# assumed the employer went quiet — user-requested 2026-08-30. Matches
# "Ігнор"'s own established meaning in this project (see
# jobsearch-status-semantics memory: "employer went silent", never "I
# missed it"), so auto-transitioning into it after enough silence is
# exactly what that status is for, not a stretch of it.
AUTO_IGNORE_APPLIED_AFTER_MONTHS = 2


def _auto_ignore_anchor_sql() -> str:
    """The later of (a) when the status became "applied" and (b) the
    listing's own application deadline, if any — SQLite's multi-arg max()
    picks the larger per row. Rationale: if the deadline is still ahead
    even after applying, the employer plausibly hasn't started reviewing
    yet, so the silence clock shouldn't start until the deadline itself
    passes; if the deadline already passed (or there wasn't one), the
    clock starts at the application itself.

    Falls back to the deadline ALONE when applied_at is unknown (rows that
    were already "applied" before that column existed) — user-requested
    2026-08-30: the deadline is real, recorded data, not a guess, so it's
    fine to anchor on it even with no known application date. Only when
    BOTH are unknown does this genuinely have nothing to count from."""
    return (
        "case when applied_at is not null "
        "then max(date(applied_at), coalesce(application_due_sort, date(applied_at))) "
        "else application_due_sort end"
    )


def auto_ignore_stale_applications(conn: sqlite3.Connection) -> int:
    """Moves "applied" ("Відгукнувся") rows to "ignored" once
    AUTO_IGNORE_APPLIED_AFTER_MONTHS have passed since the later of
    applying or the deadline — see AUTO_IGNORE_APPLIED_AFTER_MONTHS's and
    _auto_ignore_anchor_sql's own comments. A row with neither applied_at
    nor application_due_sort never matches, on purpose: no recorded date
    to count from, so no guess."""
    cur = conn.execute(
        f"""
        UPDATE vacancies
        SET user_status = 'ignored'
        WHERE user_status = 'applied'
          AND (applied_at IS NOT NULL OR application_due_sort IS NOT NULL)
          AND date('now') >= date({_auto_ignore_anchor_sql()}, '+{AUTO_IGNORE_APPLIED_AFTER_MONTHS} months')
        """
    )
    conn.commit()
    return cur.rowcount


def get_auto_ignore_date(conn: sqlite3.Connection, applied_at: str | None, application_due_sort: str | None) -> str | None:
    """YYYY-MM-DD the row will auto-ignore on if it's still "applied" then
    — for display on the detail page. None when neither applied_at nor
    application_due_sort is known (see auto_ignore_stale_applications).
    Computed via the same anchor logic the actual UPDATE uses, so the
    displayed date can never drift from what actually happens."""
    if not applied_at and not application_due_sort:
        return None
    anchor = (
        f"max(date(?), coalesce(?, date(?)))" if applied_at
        else "?"
    )
    params = (applied_at, application_due_sort, applied_at) if applied_at else (application_due_sort,)
    row = conn.execute(
        f"SELECT date({anchor}, '+{AUTO_IGNORE_APPLIED_AFTER_MONTHS} months')", params,
    ).fetchone()
    return row[0] if row else None
