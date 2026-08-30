import html
import json
import re
import sys
import uuid as uuid_module
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import requests
from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import db
import easycruit_client
import finn_client
import jobbnorge_client
import linkedin_client
import nav_client
import reachability
import scoring
from resume_prompt import build_resume_prompt
from web.render import sanitize_description

SYNC_STATE_KEY = "web_last_sync_summary"
# The "at" timestamp of the sync BEFORE the one just completed — lets index()
# mark vacancies discovered during the most recent sync as "new" without a
# separate history table (first_seen_at > this value = appeared this sync).
PREV_SYNC_AT_KEY = "web_prev_sync_at"
NEW_HIGH_SCORE_THRESHOLD = 55
# Which of the two fully-precomputed scoring profiles (db.SCORE_PROFILE_COLUMNS)
# drives the visible list/sort/filter and detail-page score — user toggle,
# 2026-08-26, persisted so it survives across requests/tabs like other feed_state.
SCORE_PROFILE_KEY = "web_score_profile"


def get_score_profile(conn) -> str:
    value = db.get_state(conn, SCORE_PROFILE_KEY)
    return value if value in db.SCORE_PROFILE_COLUMNS else "warehouse"

app = FastAPI(title="Jobsearch Norway")
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

# Cache-busting query param for static assets, computed from the file's own
# mtime — user-reported 2026-07-29: new CSS (status colors) wasn't showing
# up in their real browser after a code change, because FastAPI's
# StaticFiles serves plain Last-Modified/ETag headers with no explicit
# Cache-Control, and browsers apply their own heuristic freshness on top of
# that rather than always revalidating. `?v=<mtime>` forces a brand-new URL
# whenever style.css actually changes, so the browser can't serve a stale
# cached copy regardless of its own heuristics. Computed once at import
# time — fine since uvicorn --reload restarts the whole process (and
# re-imports this module) on every file change anyway.
STATIC_VERSION = int((STATIC_DIR / "style.css").stat().st_mtime)
templates.env.globals["STATIC_VERSION"] = STATIC_VERSION

ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")

# applicationDue arrives in whatever shape each source's own feed uses — live
# survey of the DB 2026-08-08 found ISO with a time component ("2026-08-18T00:
# 00:00Z"), dd.mm.yyyy, dd-mm-yyyy, and unpadded d.m.yyyy all active at once,
# alongside genuine free text ("Snarest", "Vi vurderer kandidater fortløpende!")
# that isn't a date at all. Mixed formats sitting next to each other in the
# same list is what actually reads as messy — user-requested 2026-08-08:
# normalize every parseable one to dd.mm.yyyy, leave free text untouched since
# there's no date in it to reformat.
DUE_DATE_PATTERNS = [
    re.compile(r"^(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})"),       # ISO, optional trailing Thh:mm:ssZ
    re.compile(r"^(?P<d>\d{1,2})-(?P<m>\d{1,2})-(?P<y>\d{4})$"),  # dd-mm-yyyy
    re.compile(r"^(?P<d>\d{1,2})\.(?P<m>\d{1,2})\.(?P<y>\d{4})$"),  # d.m.yyyy / dd.mm.yyyy
]


def format_due(value: str | None) -> str | None:
    """applicationDue from NAV isn't guaranteed to be a date — some employers put
    free text there instead (e.g. "We are evaluating candidates continuously!").
    Only reformat when it actually looks like a date; free text passes through
    unchanged (translate_value handles the handful of known recurring phrases)."""
    if not value:
        return None
    for pattern in DUE_DATE_PATTERNS:
        m = pattern.match(value)
        if m:
            return f"{int(m['d']):02d}.{int(m['m']):02d}.{m['y']}"
    return value


templates.env.filters["format_due"] = format_due


def days_until(value: str | None) -> int | None:
    """Days from today to an ISO application_due date, negative if past.
    None for free-text deadlines ("Løpende") — those never get the urgency
    badge, since there's no date to be urgent about."""
    if not value or not ISO_DATE_RE.match(value):
        return None
    try:
        due = datetime.strptime(value[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    return (due - datetime.now().date()).days


templates.env.filters["days_until"] = days_until


def format_duration_min(minutes: int | None) -> str:
    if minutes is None:
        return "—"
    h, m = divmod(minutes, 60)
    return f"{h} год {m} хв" if h else f"{m} хв"


templates.env.filters["format_duration_min"] = format_duration_min

# Raw source values are Norwegian and look like an untranslated mess sitting
# next to Ukrainian labels ("Дедлайн: Løpende", "Обсяг: Heltid"). Translate
# the handful of values that actually recur; anything not in the dict is
# shown as-is rather than swallowed, since a missing translation is better
# surfaced than hidden.
VALUE_LABELS = {
    "Heltid": "Повна ставка",
    "Deltid": "Часткова ставка",
    "Fast prosent": "Постійна (%)",
    "Fast": "Постійна посада",
    "Vikariat": "Заміщення (vikariat)",
    "Engasjement": "Тимчасове залучення",
    "Lærling": "Учнівство (lærling)",
    "Løpende": "Без фіксованого дедлайну",
    "Sesong": "Сезонна робота",
    "Prosjekt": "Проєкт",
}


def translate_value(value: str | None) -> str | None:
    if not value:
        return value
    return VALUE_LABELS.get(value, value)


templates.env.filters["translate_value"] = translate_value

STATUS_LABELS = {
    "new": "Нове",
    "interesting": "Цікаво",
    "applied": "Відгукнувся",
    "interview": "Співбесіда",
    "offer": "Оффер",
    "rejected": "Відмова",
    "ignored": "Ігнор",
    "archived": "Смітник",
}
templates.env.globals["STATUS_LABELS"] = STATUS_LABELS
templates.env.globals["USER_STATUSES"] = db.USER_STATUSES

# NAV's own level1 occupation taxonomy (occupation_categories column) —
# fixed list rather than a DISTINCT query, since it's NAV's own controlled
# vocabulary, not user-generated data (measured live 2026-08-18: exactly
# these 13 values across ~9000 active/inactive rows).
OCCUPATION_CATEGORIES = [
    "Bygg og anlegg", "Håndverkere", "Helse og sosial", "Industri og produksjon",
    "IT", "Kontor og økonomi", "Kultur og kreative yrker", "Natur og miljø",
    "Reiseliv og mat", "Salg og service", "Sikkerhet og beredskap",
    "Transport og lager", "Utdanning",
    # Found 2026-08-30 (/fullreview deep, Stage 4) — NAV's own catch-all for
    # postings it couldn't classify. Missing from this fixed copy since
    # whenever it was first written; 3 live active vacancies carry it and
    # were unreachable via this filter dropdown until now.
    "Uoppgitt/ ikke identifiserbare",
]
templates.env.globals["OCCUPATION_CATEGORIES"] = OCCUPATION_CATEGORIES

BREAKDOWN_LABELS = {
    "language_bonus": "Мова оголошення (англійська)",
    "track_it_support": "IT-support ключові слова",
    "track_general_entry_level": "Entry-level (виробництво/склад/логістика)",
    "track_dev_security": "Dev/security ключові слова",
    "entry_level_bonus": "Явно entry-level / без досвіду",
    "senior_penalty": "Senior / вимога років досвіду",
    "management_penalty": "Вимога управління персоналом",
    "remote_bonus": "Remote / hjemmekontor",
    "location_bonus": "Локація",
    "car_penalty": "Вимога власної машини",
    "degree_penalty": "Вимога формального диплома/fagbrev",
    "norwegian_fluency_penalty": "Вимога вільної норвезької",
    "occupation_category_bonus": "Категорія професії (NAV)",
    "phone_support_penalty": "Телефонний канал підтримки",
    "dev_title_penalty": "Вакансія розробника (за назвою)",
    "relocation_worthiness_penalty": "Переїзд не виправданий (частина ставки / короткий vikariat)",
    "formal_qualification_penalty": "Формальна кваліфікація (fagbrev/диплом), не IT-профіль",
    "programming_experience_penalty": "Вимагає досвід програмування",
}
BREAKDOWN_ORDER = list(BREAKDOWN_LABELS.keys())


def _detail_text(entry: dict) -> str:
    matched = entry.get("matched")
    if isinstance(matched, list):
        return ", ".join(matched) if matched else "—"
    if isinstance(matched, dict):
        flags = [k for k, v in matched.items() if v]
        return ", ".join(flags) if flags else "—"
    if "reason" in entry:
        return entry["reason"]
    if matched:
        return "так"
    return "—"


def format_breakdown(raw: str | None) -> list[dict]:
    if not raw:
        return []
    data = json.loads(raw)
    return [
        {"label": BREAKDOWN_LABELS[key], "points": data[key]["points"], "detail_text": _detail_text(data[key])}
        for key in BREAKDOWN_ORDER
        if key in data
    ]


def get_conn():
    return db.connect()


def get_vacancy_or_404(conn, uuid: str):
    """Was copy-pasted (vacancy = db.get_vacancy(...); if vacancy is None:
    return 404) across 4 routes — one place to fix if the not-found
    response ever needs to change (code-review 2026-07-19)."""
    vacancy = db.get_vacancy(conn, uuid)
    if vacancy is None:
        raise HTTPException(status_code=404, detail="Vacancy not found")
    return vacancy


PAGE_SIZE = 50
GENERATED_DIR = (Path(__file__).parent.parent / "profile" / "generated").resolve()
# .docx download buttons removed 2026-07-20 (user: "не бачу в них сенсу") —
# .docx is just the editable intermediate the pipeline works from; .pdf is
# what actually gets submitted, and that's the only thing worth surfacing
# in the UI. cv.pdf may also come from a per-vacancy CV placed via the
# place-cv skill rather than generated by cv_builder.py.
GENERATED_DOC_NAMES = ("cv.pdf", "soknad.pdf")

# --- Manual "Додати вакансію" add-form (2026-08-30, user-requested) --------
# Direct browser URL shape ("linkedin.com/jobs/view/{id}[-slug]"), distinct
# from linkedin_client.VIEW_JOB_RE's email-digest shape
# ("linkedin.com/comm/jobs/view/{id}") — this is what the user actually
# copies from their address bar.
LINKEDIN_JOB_VIEW_URL_RE = re.compile(r"linkedin\.com/jobs/view/(?:[\w-]*-)?(\d+)")
# LinkedIn's public (unauthenticated) job page renders this as
# "{Employer} hiring {Title} in {Municipal}, {County}, Norway | LinkedIn".
LINKEDIN_OG_TITLE_RE = re.compile(r"^(.*?) hiring (.*?) in (.*?)\s*\|\s*LinkedIn$")
LINKEDIN_FETCH_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def fetch_linkedin_preview(url: str) -> dict:
    """One-off fetch of a single public LinkedIn job page a human pasted
    into the add-vacancy form — deliberately NOT part of linkedin_client.py
    (whose own docstring states it "never touches linkedin.com's own
    servers" as a matter of policy, for the automated sync pipeline: bulk/
    scripted access tied to the user's account is what risks the 48-hour
    suspension detection researched in jobsearch-linkedin memory). This is
    a fundamentally different risk shape: one unauthenticated GET of a page
    the user already has open in their own browser, no login, no session,
    no repeating pattern — the same request a search-engine crawler or the
    "og:" social-preview tags themselves are designed for.

    Plain requests — no JS execution, so LinkedIn's guest view only exposes
    title/employer/location via the og:title meta tag, not the full
    description (that's rendered client-side). The user pastes the rest by
    hand, same as the description-borrowing fallback the automated LinkedIn
    sync already relies on for every row (see scoring._build_description_
    lender_lookup)."""
    resp = requests.get(url, headers={"User-Agent": LINKEDIN_FETCH_USER_AGENT}, timeout=15)
    resp.raise_for_status()
    m = re.search(r'<meta property="og:title" content="([^"]*)"', resp.text)
    if not m:
        raise ValueError("Не знайшов og:title на цій сторінці — це точно посилання на вакансію LinkedIn?")
    og_title = html.unescape(m.group(1))
    parsed = LINKEDIN_OG_TITLE_RE.match(og_title)
    if not parsed:
        return {"title": og_title, "business_name": "", "municipal": "", "county": ""}
    employer, title, location = (s.strip() for s in parsed.groups())
    municipal = location.split(",")[0].strip()
    county = jobbnorge_client._build_municipality_county_map().get(municipal.upper(), "")
    return {"title": title, "business_name": employer, "municipal": municipal, "county": county}


@app.get("/", response_class=HTMLResponse)
def index(
    request: Request, user_status: list[str] = Query(default=[]), language: str = "", q: str = "",
    source: str = "", show_excluded: str = "", page: int = 1,
    min_score: str = "", min_salary: str = "", sort: str = "score",
    show_flagged: str = "", min_extent_percent: str = "", occupation_category: str = "",
):
    conn = get_conn()
    score_profile = get_score_profile(conn)
    show_excluded_flag = show_excluded == "1"
    show_flagged_flag = show_flagged == "1"
    page = max(1, page)
    # Plain str query params instead of int, so an empty field round-trips
    # as "" (no filter) rather than FastAPI 422ing on int("") — a blank
    # filter is the resting state of these inputs, not user error.
    min_score_val = int(min_score) if min_score.strip().isdigit() else None
    min_salary_val = int(min_salary) if min_salary.strip().isdigit() else None
    min_extent_percent_val = int(min_extent_percent) if min_extent_percent.strip().isdigit() else None
    sort = sort if sort == "deadline" else "score"
    # active_only=True always — db._vacancy_filters bakes the "a reacted
    # vacancy stays visible even after the source closes it" exemption
    # into the WHERE clause itself (2026-08-16), so it now applies
    # unconditionally: default browsing, search, and any status filter
    # combination all show applied/interview/offer/rejected/ignored/
    # archived rows regardless of the listing's own ACTIVE/INACTIVE state.
    # Only "new" and "interesting" stay subject to the ACTIVE-only rule —
    # that's the open backlog, not tracked application history. See that
    # function's comment for the live report that prompted this.
    filter_kwargs = dict(
        active_only=True,
        user_status=user_status or None,
        language=language or None,
        search=q or None,
        source=source or None,
        show_excluded=show_excluded_flag,
        min_score=min_score_val,
        min_salary=min_salary_val,
        show_flagged=show_flagged_flag,
        min_extent_percent=min_extent_percent_val,
        occupation_category=occupation_category or None,
        score_profile=score_profile,
    )
    total = db.count_vacancies(conn, **filter_kwargs)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(page, total_pages)
    vacancies = db.list_vacancies(conn, limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE, sort=sort, **filter_kwargs)
    last_sync = db.get_state(conn, SYNC_STATE_KEY)
    new_since = db.get_state(conn, PREV_SYNC_AT_KEY)
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "vacancies": vacancies,
            "filters": {
                "user_status": user_status, "language": language, "q": q, "source": source,
                "min_score": min_score, "min_salary": min_salary, "sort": sort,
                "min_extent_percent": min_extent_percent, "occupation_category": occupation_category,
            },
            "sources": db.list_sources(conn),
            "last_sync": json.loads(last_sync) if last_sync else None,
            "new_since": new_since,
            "excluded_count": db.count_excluded(conn),
            "show_excluded": show_excluded_flag,
            "flagged_count": db.count_flagged(conn),
            "show_flagged": show_flagged_flag,
            "page": page,
            "total_pages": total_pages,
            "total": total,
            "score_profile": score_profile,
        },
    )


@app.get("/kanban", response_class=HTMLResponse)
def kanban(request: Request):
    conn = get_conn()
    score_profile = get_score_profile(conn)
    # "new" is the entire unreacted backlog (~1000s of rows) — that's the
    # main list's job. Kanban is for tracking applications you've actually
    # acted on, so it starts at "interesting". "ignored"/"archived" are
    # terminal dead-end states (user-requested 2026-08-02: they clutter the
    # active-pipeline board) — still reachable via the main list's status
    # filter, just not a kanban column. active_only=False: a vacancy you've
    # applied to still matters after the posting itself closes.
    # show_flagged=True: flagging (🚩) and application status are
    # independent axes — a vacancy you're already tracking here must not
    # silently vanish just because you also reported a data bug on it
    # (code-review 2026-07-19; index.html shows a "hidden, show anyway" bar
    # for the same case, kanban has none, so hiding here would be silent).
    columns = {
        s: db.list_vacancies(conn, active_only=False, user_status=s, limit=500, show_flagged=True, score_profile=score_profile)
        for s in db.USER_STATUSES if s not in ("new", "ignored", "archived")
    }
    return templates.TemplateResponse(request, "kanban.html", {"columns": columns, "score_profile": score_profile})


@app.post("/sync")
def trigger_sync():
    # Snapshot the whole DB before touching it — see db.backup_db's own
    # docstring for why (2026-07-29 incident: no backup existed, ~50
    # vacancies' user_status got scrambled by an unrelated mistake with no
    # way to recover the prior values). Best-effort: a backup failure
    # shouldn't block the sync itself.
    backup_failed = None
    try:
        db.backup_db()
    except OSError as e:
        backup_failed = str(e)

    conn = get_conn()
    # Read before overwriting: this becomes the "new since" watermark for the
    # index page. Must be UTC, not "at" (local display time) — first_seen_at
    # is written by SQLite's datetime('now'), which is UTC. Comparing a local
    # (CEST, UTC+2 in summer) watermark against a UTC column as plain strings
    # would silently miss "new" vacancies for up to 2 hours after every sync.
    prior_summary_raw = db.get_state(conn, SYNC_STATE_KEY)
    prior_watermark_utc = json.loads(prior_summary_raw).get("watermark_utc") if prior_summary_raw else None

    nav_stats = nav_client.sync(conn)
    jobbnorge_stats = jobbnorge_client.sync(conn)
    try:
        finn_stats = finn_client.sync(conn)
    except Exception as e:
        # Gmail auth can expire/break independently of everything else here —
        # one source failing must not take down NAV/Jobbnorge sync with it.
        finn_stats = {"error": str(e)}
    try:
        easycruit_stats = easycruit_client.sync(conn)
    except Exception as e:
        easycruit_stats = {"error": str(e)}
    try:
        linkedin_stats = linkedin_client.sync(conn)
    except Exception as e:
        # Same Gmail-auth-can-expire reasoning as finn — independent of
        # NAV/Jobbnorge/finn/easycruit, must not take the rest down with it.
        linkedin_stats = {"error": str(e)}
    scored = scoring.rescore_all(conn)
    deleted_inactive = db.delete_inactive(conn)
    deleted_expired = db.delete_expired_unreacted(conn)
    deleted_archived = db.delete_archived(conn)
    auto_ignored = db.auto_ignore_stale_applications(conn)
    new_high_score = (
        db.count_new_high_score(conn, prior_watermark_utc, NEW_HIGH_SCORE_THRESHOLD)
        if prior_watermark_utc else 0
    )
    summary = {
        "at": datetime.now().strftime("%d.%m.%Y %H:%M"),  # local time, display only
        "watermark_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "stats": nav_stats,
        "jobbnorge": jobbnorge_stats,
        "finn": finn_stats,
        "easycruit": easycruit_stats,
        "linkedin": linkedin_stats,
        "scored": scored,
        "deleted_inactive": deleted_inactive,
        "deleted_expired": deleted_expired,
        "deleted_archived": deleted_archived,
        "auto_ignored": auto_ignored,
        "new_high_score": new_high_score,
        "backup_failed": backup_failed,
    }
    db.set_state(conn, SYNC_STATE_KEY, json.dumps(summary, ensure_ascii=False))
    if prior_watermark_utc:
        db.set_state(conn, PREV_SYNC_AT_KEY, prior_watermark_utc)
    return RedirectResponse(url="/", status_code=303)


@app.post("/score-profile")
def set_score_profile(profile: str = Form(...), next: str = Form("/")):
    if profile not in db.SCORE_PROFILE_COLUMNS:
        raise HTTPException(status_code=400, detail="Unknown score profile")
    conn = get_conn()
    db.set_state(conn, SCORE_PROFILE_KEY, profile)
    return RedirectResponse(url=next, status_code=303)


@app.get("/sync-status")
def sync_status():
    """Polled by index.html to auto-reload OTHER open tabs once a sync
    finishes — the tab that submits the sync form already gets a fresh page
    for free via the 303 redirect above; this is for a second tab left open
    elsewhere that has no way to know a sync happened without it."""
    conn = get_conn()
    last_sync = db.get_state(conn, SYNC_STATE_KEY)
    watermark = json.loads(last_sync)["watermark_utc"] if last_sync else None
    return {"watermark_utc": watermark}


@app.get("/vacancy/add", response_class=HTMLResponse)
def add_vacancy_form(request: Request):
    """Declared BEFORE /vacancy/{uuid} below — FastAPI matches routes in
    declaration order, and {uuid} would otherwise swallow "add" as if it
    were a vacancy id."""
    conn = get_conn()
    return templates.TemplateResponse(
        request, "add_vacancy.html",
        {"score_profile": get_score_profile(conn), "values": None, "error": None},
    )


@app.post("/vacancy/add/linkedin-preview", response_class=HTMLResponse)
def add_vacancy_linkedin_preview(request: Request, link: str = Form("")):
    """HTMX partial — fetches title/employer/location from a pasted
    LinkedIn job URL and re-renders the form fields pre-filled. See
    fetch_linkedin_preview's own docstring for why this one-off fetch is a
    different call than linkedin_client.py's automated-sync policy."""
    link = link.strip()
    values = {"link": link}
    error = None
    if not link:
        error = "Встав посилання на вакансію LinkedIn спочатку."
    else:
        try:
            values.update(fetch_linkedin_preview(link))
        except requests.RequestException as e:
            error = f"Не вдалось відкрити посилання: {e}"
        except ValueError as e:
            error = str(e)
    return templates.TemplateResponse(
        request, "_add_vacancy_fields.html", {"values": values, "error": error},
    )


@app.post("/vacancy/add")
def add_vacancy_submit(
    link: str = Form(""), title: str = Form(...), business_name: str = Form(""),
    municipal: str = Form(""), county: str = Form(""), description: str = Form(""),
    application_due: str = Form(""), user_status: str = Form("new"),
):
    conn = get_conn()
    link = link.strip()
    job_id_match = LINKEDIN_JOB_VIEW_URL_RE.search(link) if link else None
    if job_id_match:
        new_uuid = f"linkedin-{job_id_match.group(1)}"
        source = "linkedin"
    else:
        # No recognized source in the link (or no link at all) — a plain
        # manually-entered listing, same "manual" source value already
        # used by a couple of pre-existing hand-added rows.
        new_uuid = f"manual-{uuid_module.uuid4().hex[:12]}"
        source = "manual"
    row = {
        "uuid": new_uuid,
        "status": "ACTIVE",
        "title": title.strip(),
        "business_name": business_name.strip() or None,
        "employer_name": business_name.strip() or None,
        "municipal": municipal.strip() or None,
        "county": county.strip() or None,
        "description": description.strip() or None,
        "application_url": link or None,
        "application_due": application_due.strip() or None,
        "link": link or None,
        "engagement_type": None,
        "extent": None,
        "sector": None,
    }
    db.upsert_vacancy_row(conn, row, source=source)
    if user_status in db.USER_STATUSES:
        db.set_user_status(conn, new_uuid, user_status)
    scoring.rescore_one(conn, new_uuid)
    return RedirectResponse(url=f"/vacancy/{new_uuid}", status_code=303)


@app.get("/vacancy/{uuid}", response_class=HTMLResponse)
def vacancy_detail(request: Request, uuid: str):
    conn = get_conn()
    score_profile = get_score_profile(conn)
    vacancy = get_vacancy_or_404(conn, uuid)
    description_html = sanitize_description(vacancy["description"])
    if score_profile == "it":
        display_score, breakdown_raw = vacancy["score_it"], vacancy["score_it_breakdown"]
    else:
        display_score, breakdown_raw = vacancy["score"], vacancy["score_breakdown"]
    breakdown = format_breakdown(breakdown_raw)
    doc_dir = GENERATED_DIR / uuid
    generated_docs = [name for name in GENERATED_DOC_NAMES if (doc_dir / name).exists()]
    reach = reachability.get_reachability(conn, vacancy["municipal"])
    lender = db.get_vacancy(conn, vacancy["description_borrowed_from"]) if vacancy["description_borrowed_from"] else None
    auto_ignore_date = (
        db.get_auto_ignore_date(conn, vacancy["applied_at"], vacancy["application_due_sort"])
        if vacancy["user_status"] == "applied" else None
    )
    return templates.TemplateResponse(
        request,
        "detail.html",
        {
            "v": vacancy, "description_html": description_html, "breakdown": breakdown,
            "generated_docs": generated_docs, "reach": reach, "lender": lender,
            "score_profile": score_profile, "display_score": display_score,
            "auto_ignore_date": auto_ignore_date,
        },
    )


@app.get("/vacancy/{uuid}/document/{filename}")
def download_document(uuid: str, filename: str):
    if filename not in GENERATED_DOC_NAMES:
        return HTMLResponse("Not found", status_code=404)
    path = (GENERATED_DIR / uuid / filename).resolve()
    # uuid is a path component built from an external source (NAV/Jobbnorge/
    # finn IDs) — cheap to double-check it can't escape GENERATED_DIR via
    # something like "../../" before touching the filesystem.
    if not path.is_relative_to(GENERATED_DIR) or not path.exists():
        return HTMLResponse("Not found", status_code=404)
    return FileResponse(path, filename=filename)


@app.get("/vacancy/{uuid}/resume-prompt", response_class=HTMLResponse)
def vacancy_resume_prompt(request: Request, uuid: str, lang: str = "en"):
    lang = "no" if lang == "no" else "en"
    conn = get_conn()
    vacancy = get_vacancy_or_404(conn, uuid)
    prompt = build_resume_prompt(
        vacancy["title"], vacancy["description"], vacancy["employer_name"],
        vacancy["municipal"], vacancy["county"], uuid, lang=lang,
    )
    return templates.TemplateResponse(
        request, "resume_prompt.html",
        {"v": vacancy, "prompt": prompt, "lang": lang, "score_profile": get_score_profile(conn)},
    )


@app.post("/vacancy/{uuid}/status", response_class=HTMLResponse)
def update_status(request: Request, uuid: str, user_status: str = Form(...)):
    conn = get_conn()
    get_vacancy_or_404(conn, uuid)
    db.set_user_status(conn, uuid, user_status)
    vacancy = db.get_vacancy(conn, uuid)
    return templates.TemplateResponse(
        request, "_status_control.html", {"v": vacancy}
    )


@app.post("/vacancy/{uuid}/flag", response_class=HTMLResponse)
def toggle_flag(request: Request, uuid: str):
    conn = get_conn()
    vacancy = get_vacancy_or_404(conn, uuid)
    db.set_flagged(conn, uuid, flagged=vacancy["flagged_at"] is None)
    vacancy = db.get_vacancy(conn, uuid)
    return templates.TemplateResponse(
        request, "_flag_control.html", {"v": vacancy}
    )


@app.post("/vacancy/{uuid}/notes", response_class=HTMLResponse)
def update_notes(request: Request, uuid: str, notes: str = Form("")):
    conn = get_conn()
    get_vacancy_or_404(conn, uuid)
    db.set_notes(conn, uuid, notes.strip())
    vacancy = db.get_vacancy(conn, uuid)
    return templates.TemplateResponse(
        request, "_notes_control.html", {"v": vacancy}
    )
