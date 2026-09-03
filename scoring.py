"""Rule-based match scoring for vacancies against profile/profile.md.

Keyword-based first pass (cheap, runs on every vacancy). An LLM-based second
pass over the top candidates is a later stage — see profile/cv-reference.md
and the project plan discussed with the user. Every rule here traces back to
a decision made with the user on 2026-07-16—2026-07-17 (see the
jobsearch-norway-profile memory file); don't add a signal here without a
matching entry there.
"""

import json
import re

from db import strip_html
from hard_blocks import REQUIREMENT_VERB_RE, has_optional_marker, iter_requirement_clauses

# --- Track A: IT-support / helpdesk / servicedesk --------------------------
# Backed by 3+ years of real experience (Verna, PUMB, freelance repair).
# Keywords collected from real Norwegian IT-support postings, see
# profile/cv-reference.md section 6. This is the user's stated preference,
# so it carries the highest per-hit weight.
IT_SUPPORT_KEYWORDS = [
    "it-support", "it support", "brukerstøtte", "servicedesk", "service desk",
    "helpdesk", "help desk", "supportkonsulent", "it-konsulent",
    "1. linje", "1.linje", "førstelinje", "2. linje", "2.linje", "andrelinje",
    "3. linje", "3.linje", "tredjelinje",
    "feilsøking", "troubleshooting", "on-site support",
    "windows", "microsoft 365", "office 365", "active directory", "azure ad",
    "entra id", "client management", "servicenow", "jira service", "topdesk",
    "ticketing", "teknisk support", "teknisk brukerstøtte",
    # Added 2026-08-16: job-title compounds for the exact roles being applied
    # to (Duell "Supportmedarbeider", Tieto "supportspesialist") — both
    # scored via unrelated body-text keywords, and Tieto matched nothing at
    # all (0/40 on the support track for a pure support vacancy). Kept to
    # narrow compounds deliberately: bare "kundestøtte"/"feilsøke" were
    # tried and reverted — they pulled in a bike mechanic, a Fast Food
    # Service technician, and a telemarketer (audited 2026-08-16).
    "supportspesialist", "supportmedarbeider",
]

# --- Track B: general entry-level (production / warehouse / logistics) -----
# Retargeted 2026-08-18 — user explicitly rejected retail/shop-assistant and
# restaurant/hotel work ("вакансії продавця теж нахуй, я не буду"; їдальні/
# ресторани/готелі/магазини "в останню чергу"), overriding the 2026-07-17
# rationale below this list used to cite (Miniso retail experience). Retail
# keywords (butikkmedarbeider/selger/salg/resepsjon(ist)/retail/receptionist/
# sales assistant/shop assistant) removed. Replaced with production/
# warehouse/logistics terms backed by the same real experience angle (Verna:
# delivery, install, on-site tech service) plus the user's own steer toward
# factory/warehouse work. Each addition checked against the live corpus
# first (same discipline as the REMOTE_KEYWORDS fixes): "matproduksjon" and
# "varemottak" were tried and DROPPED — both are generic enough to appear
# constantly in kitchen/restaurant/café job ads ("Kokk", "kjøkkensjef",
# "tilkallingsvikar på kjøkkenet"), exactly the category just rejected.
# "industrimekaniker" was also dropped — it's itself a fagbrev-gated
# skilled trade (same class as elektriker/tømrer in hard_blocks.py), not an
# entry-level role; scoring it up would fight hard_blocks' own judgment on
# lookalike titles.
GENERAL_ENTRY_KEYWORDS = [
    "kundeservice", "kundebehandling", "lagermedarbeider", "logistikk",
    "ekstrahjelp", "sommerjobb", "montør", "vaktmester",
    "teknisk service", "customer service", "warehouse",
    "produksjonsmedarbeider", "produksjonsarbeider", "prosessoperatør",
    "maskinoperatør", "lagerarbeid", "logistikkoperatør",
    "terminalarbeid", "pakkeri",
]
# "truckfører"/"truckførerbevis" removed 2026-08-29 — the user isn't
# pursuing the certificate independently (see hard_blocks.py's
# TRUCKFORERBEVIS block, added 2026-08-26), so a mention of it, soft or
# hard, is never actually a point in the vacancy's favor; a hard
# requirement is now a full exclusion, and a soft mention shouldn't earn a
# bonus for a certificate the user doesn't have either.
#
# "vikar"/"deltid" moved OUT to GENERAL_ENTRY_TITLE_KEYWORDS below
# 2026-08-29 — same "bare word needs to describe the position itself, not
# just appear somewhere in the body" lesson as junior/trainee
# (ENTRY_LEVEL_TITLE_KEYWORDS above). Live audit: "vikar" matched 2377
# active ads' bodies but only 39% also had it in the title — the rest was
# generic HR boilerplate ("midlertidig engasjerte eller vikarer som har
# vært...", a legal-notice paragraph) or employee-benefits text
# ("fleksitid, deltid o.l."), nothing to do with the vacancy's own nature.
# Caught concretely on UiT's "Ledig stilling innen IT-infrastruktur"
# (Overingeniør, 3-year project post) picking up +12 from exactly this
# boilerplate.
GENERAL_ENTRY_TITLE_KEYWORDS = ["vikar", "deltid"]
# NOTE: bare "lager" (meant as "warehouse") deliberately excluded — live
# false-positive audit 2026-07-18 found it matching the Norwegian verb
# "å lage" ("to make"), conjugated "lager" ("makes"), in ordinary business
# copy ("produktene vi lager skal gi verdi...", 7+ hits), a building name
# ("havnelageret" — an office building, not a warehouse job), and even
# "klager" (complaints/appeals — an unrelated word that merely contains the
# substring). Of 56 vacancies matching bare "lager" without also matching
# "lagermedarbeider", the clear majority were this kind of noise; the
# genuine warehouse-adjacent postings mostly also matched "logistikk"
# already. Same "single ambiguous word needs a compound/phrase instead"
# lesson as the junior/trainee split above.

# --- Track C: dev / cybersecurity ------------------------------------------
# Backed by education (bachelor's, unrecognized in Norway) but zero
# professional experience — lower weight until diploma recognition / asylum
# decision. See jobsearch-norway-profile memory, "Напрямок пошуку" section.
DEV_SECURITY_KEYWORDS = [
    "python", "backend", "developer", "utvikler", "programmer", "programmerer",
    "cybersecurity", "cyber security", "informasjonssikkerhet", "sikkerhet",
    "software engineer", "software developer",
]

# Specific enough phrases that they're safe to match anywhere in the body —
# unlike a bare "junior"/"trainee", these don't false-positive on ads that
# merely *mention* junior colleagues the hire would work alongside (e.g. a
# senior role that "supports junior engineers" — matched live, see git log /
# jobsearch-norway-sources memory for the case that prompted this split).
ENTRY_LEVEL_PHRASES = [
    "entry level", "entry-level", "nyutdannet", "ingen erfaring",
    "ingen krav til erfaring", "no experience required",
    "on-the-job training",
]

# Single-word markers ("junior", "trainee") are only trustworthy when they
# describe the position itself, i.e. appear in the title — in body text they
# too easily match mentoring/team-composition mentions instead.
# NOTE: "lærling" deliberately excluded — see hard_blocks.py. A Norwegian
# apprenticeship is not an entry-level opportunity for this profile; it
# requires completed Vg1+Vg2 videregående skole in the trade first. Live bug
# 2026-07-17: 10 lærling postings were scoring 22% as "good entry-level
# options" via this bonus when they were actually zero-chance.
ENTRY_LEVEL_TITLE_KEYWORDS = ["junior", "trainee"]

# Title-level seniority markers — checked against the title specifically
# (not the full body) to avoid false positives from unrelated mentions of
# "senior management" etc. elsewhere in the ad text.
SENIOR_TITLE_KEYWORDS = [
    "senior", "lead ", "teamlead", "team lead", "avdelingsleder",
    "erfaren ",
]

YEARS_EXPERIENCE_PATTERNS = [
    r"minimum \d+ år(s)? erfaring",
    r"\d+\+? år(s)? erfaring",
    r"\d+\+ years('? )?( of)? experience",
    r"flere års erfaring",
]

# Title-level "this role IS a developer role" markers — user-requested
# 2026-08-29 ("я не розробник, якщо це основна ціль вакансії — мені це не
# треба"): a big penalty when the JOB ITSELF is a dev/data-engineering
# role, independent of and on top of DEV_SECURITY_KEYWORDS' own body-text
# bonus (that bonus is for a support/ops role that merely *mentions*
# Python/scripting as a nice-to-have skill — a genuinely different signal
# from the role's own title). Title-only, same reasoning as
# SENIOR_TITLE_KEYWORDS/ENTRY_LEVEL_TITLE_KEYWORDS: checked against the
# live corpus (2026-08-29, 38 dev-titled active ads) — 0 collide with
# support/servicedesk/brukerstøtte/drift titles.
DEV_TITLE_KEYWORDS = [
    "utvikler", "developer", "programmerer", "data engineer",
    "software engineer", "fullstack", "full-stack", "full stack",
    "backend", "frontend", "devops", "data scientist", "dataingeniør",
    # Added 2026-08-30 (/fullreview deep, Stage 2 check #1) — "data
    # engineer"/"software engineer" alone missed the same class of role
    # spelled with an extra word or a different noun: "Data & Analytics
    # Engineer" (47), "IT Cloud Platform Engineer" (35), "Database
    # Engineer" (27), "Analytics Engineer" (26) all slipped through
    # unpenalized. Checked against the live corpus — all 4 phrases match
    # only genuinely software/data-engineering titles, 0 collisions.
    "platform engineer", "cloud engineer", "analytics engineer", "database engineer",
]
DEV_TITLE_PENALTY = -40

# People-management requirement — checked against the BODY, not the title.
# Added 2026-08-15 after "Er du en trygg leder som brenner for å levere god
# kvalitet og service?" (Skatteetaten, uuid 601adabd) scored 63 and landed
# near the top of the shortlist. It is an underdirektør post with
# personalansvar for 16 people, i.e. a different profession, but it matched
# +32 of pure support keywords ("it-support", "brukerstøtte", "2.linje",
# "on-site support") and took no penalty at all: SENIOR_TITLE_KEYWORDS has
# "avdelingsleder" but not a bare "leder", and YEARS_EXPERIENCE_PATTERNS
# wants a digit while the ad says "lang operativ ledererfaring".
#
# The title is deliberately NOT the signal here. Adding "leder"/"manager" to
# SENIOR_TITLE_KEYWORDS would also have penalised OsloMet's "Vikariat som
# assisterende Service Desk Manager" — a coordination role with no reports,
# which was a legitimate application. What separates them is whether the ad
# demands responsibility for staff, and that only ever shows up in the body.
MANAGEMENT_REQUIRED_PATTERNS = [
    r"personalansvar",
    r"ledererfaring",
    r"lederansvar",
    r"personalledelse",
    r"budsjettansvar",
    r"resultatansvar",
]

REMOTE_KEYWORDS = [
    "location independent", "distansearbeid",
    # "hybrid"/"remote" as bare words removed 2026-08-01 (flagged-vacancy
    # queue review) — even negation-checked, they matched Storebrand's
    # "hybridordning" pension product, "hybride miljøer"/"hybrid
    # skyarkitektur" (cloud infra), "hybride trusler" (security), "hybrid
    # intelligens"/"hybrid ai-optimization" (research), "hybridundervisning"
    # (teaching mode), "hybridindustri"/hybridfartøy/hybridferje (vehicles/
    # industry), a "hybrid lederstilling" (a leadership structure, not work
    # location), an eyelash technician's "hybrid" styling technique, "remote
    # operations center"/"remote weapon station" (hardware), "remote
    # sensing" (a PhD topic), "crash logs remotely" (device monitoring), and
    # "a remote yet spectacular natural environment" (the workplace's
    # geography, not telecommuting). Two live false positives from the
    # queue: ABB "Project Engineer Automation" (Hammerfest, on-site) scored
    # +15 off "hybridindustri", and two Ytri Island Retreat chef postings
    # scored +15 off that "remote... natural environment" phrase describing
    # the island, not the job — both on-site kitchen/engineering roles. A
    # proximity-window disambiguator ("hybrid/remote near a work word") was
    # tried and reverted — "comfortable living and WORKING in a REMOTE...
    # environment" still matched, since "working" is exactly the kind of
    # generic verb the false positives use nearby. Explicit phrases below
    # instead — verified against the live corpus 2026-08-01: every one of
    # the ~100 previously-matching vacancies not covered by a phrase here
    # was a confirmed false positive (see above), none a real loss.
    "hybrid og fleksibel arbeidshverdag", "hybrid arbeidshverdag",
    "hybride arbeidsordning", "hybrid arbeidsmodell", "hybrid arbeidsdag",
    "hybrid arbeid", "hybrid kontor", "hybrid work", "hybrid working",
    "remote arbeid", "remote work", "remote-first", "remote friendly",
    "fullt remote", "100 % remote", "100% remote", "primært remote",
    # "hjemmekontor"/"fjernarbeid"/"home office" as bare words removed
    # 2026-08-15 (user-flagged false positive: Rogaland fylkeskommune
    # "IT-rådgiver i seksjon IT og brukerstøtte" — an on-site Stavanger role
    # scored 55%, +15 of it from "mulighet for hjemmekontor etter gjeldende
    # retningslinjer" buried in the generic benefits paragraph). Same
    # failure shape as "jobbe hjemmefra" below and the hybrid/remote fix
    # above — measured live against the full active corpus: 218/218 bare
    # "hjemmekontor" matches were this same perk-list pattern ("mulighet
    # for hjemmekontor", "fleksibel arbeidstid og [...] hjemmekontor",
    # "noe hjemmekontor etter avtale") except 2, both using the explicit
    # "100 % hjemmekontor" phrase kept below; "fjernarbeid" (5 matches) and
    # "home office" (1 match) showed the identical pattern, zero genuine
    # remote-first hits between them. Bare-keyword false positives in this
    # list are not a one-off — they're the default failure mode for any
    # single Norwegian/English word describing a work arrangement, since
    # employers copy-paste the same perks paragraph onto every posting
    # regardless of role. Audit every new addition here the same way.
    "fullt hjemmekontor", "100 % hjemmekontor", "100% hjemmekontor",
    "primært hjemmekontor", "utelukkende hjemmekontor", "kun hjemmekontor",
    "hjemmekontor som hovedarbeidssted", "hjemmekontor som arbeidsplass",
    "fullt fjernarbeid", "100 % fjernarbeid", "100% fjernarbeid",
    "primært fjernarbeid", "fully remote", "100% home office",
]
# "jobbe hjemmefra" removed 2026-07-21 (user-flagged false positive, live
# instance: Sweco "Industrirådgiver prosess" — an on-site industrial process
# engineering role scored 35, +15 of it from this single phrase). It's a
# generic HR flexibility-perk sentence ("du kan jobbe hjemmefra når du har
# behov for det") that gets copy-pasted into the benefits paragraph of many
# unrelated NAV postings by the same employer (43 active vacancies matched
# it — lawyer, foster-care coordinator, tax-crime investigator, archivist —
# none IT-support), not a signal the role itself is remote-friendly. The
# other keywords here describe the role's OWN nature, not an occasional
# perk, so they don't share this failure mode.

CAR_REQUIRED_KEYWORDS = [
    "egen bil", "førerkort", "eget kjøretøy", "driver's license",
    "driving licence", "must have a car",
]


def _has_unmet_car_requirement(text: str) -> bool:
    """Section/clause-aware, added 2026-08-30 (/fullreview deep, Stage 4):
    the previous bare `any(kw in text ...)` check gave the full -20 penalty
    to EVERY mention, including explicitly soft/negated ones — measured
    live: 280 of 1425 non-excluded matches for "førerkort" alone read as
    "er en fordel, men ikke et krav"/"ikke nødvendig"/similar, yet all got
    penalized identically to a hard "må ha førerkort". Same bug shape as
    the truckførerbevis/forklift-certificate checks, so it reuses their
    exact clause/section machinery (hard_blocks.iter_requirement_clauses)
    rather than reimplementing it."""
    for clause, in_required_section in iter_requirement_clauses(text):
        if not any(kw in clause for kw in CAR_REQUIRED_KEYWORDS):
            continue
        if has_optional_marker(clause):
            continue
        if REQUIREMENT_VERB_RE.search(clause) or in_required_section:
            return True
    return False

# NAV's own occupation_categories JSON (level1 only — level2 is far more
# granular than useful here) — added 2026-08-18 alongside the Track B
# retarget above. More reliable than any keyword list: NAV already
# classifies ~83% of active listings, so this catches production/warehouse
# postings the keyword track misses (measured live: keyword-only matched
# just 23% of titles under "Transport og lager"/"Industri og produksjon").
# Bonus categories mirror the retargeted GENERAL_ENTRY_KEYWORDS above;
# penalty categories are exactly what the user explicitly ruled out
# 2026-08-18 ("вакансії продавця теж нахуй"; "їдальні та ресторани, готелі
# ... магазини" — Salg og service and Reiseliv og mat, specifically named).
# Helse og sosial/Utdanning deliberately NOT penalized here — not named as
# rejected, and hard_blocks.py already excludes the regulated professions
# that dominate those categories.
OCCUPATION_CATEGORY_BONUS = {
    "Industri og produksjon": 15,
    "Transport og lager": 15,
}
OCCUPATION_CATEGORY_PENALTY = {
    "Salg og service": -15,
    "Reiseliv og mat": -15,
}

# Explicit phone-channel markers — added 2026-08-18, user wants chat/
# written support, not phone-based ("звичайну підтримку по чатам... без
# телефонного режиму"). A positive "chat support" detector was considered
# and rejected: measured live, explicit chat mentions are far too sparse
# (16/5609 active listings) and most support ads name no channel at all —
# a positive filter would hide almost everything. This penalizes only the
# postings that explicitly commit to a phone-heavy role instead.
PHONE_SUPPORT_KEYWORDS = ["kundesenter", "telefonsalg", "utgående samtaler"]

# Municipalities in/around Sogndal kommune (post-2020 merger absorbed
# Balestrand and Leikanger into Sogndal) plus the immediate Sogn neighbors —
# realistic commute/local-community radius given no driving license.
TIER_1_MUNICIPALS = {
    "SOGNDAL", "BALESTRAND", "LEIKANGER", "LUSTER", "AURLAND", "LÆRDAL",
    "VIK", "HØYANGER", "ÅRDAL",
}

# "Major job market" tier — user-requested 2026-08-29: Bergen should score
# the same as Oslo, both are the two biggest job markets in the country and
# worth relocating for even though neither is the Sogndal/Sogn home region
# (TIER_1, still the highest bonus — no relocation needed there at all).
# Oslo-region is checked at COUNTY level (matches how the user phrased it —
# "Акерсхус, Бускеруд, Ліллестрьом" are fylke/kommune names covering the
# whole commute belt, and Oslo's own county is small enough that the fylke
# itself is a reasonable proxy). Bergen-region is checked at MUNICIPAL
# level instead, like TIER_1 — Vestland fylke is far too broad to treat
# uniformly (it already gets its own smaller VESTLAND_BONUS below for
# everywhere else in the fylke, mostly remote fjord country nowhere near
# Bergen's actual job market).
TIER_2_COUNTIES = {"OSLO", "AKERSHUS", "BUSKERUD"}
TIER_2_MUNICIPALS = {
    "BERGEN", "ASKØY", "ØYGARDEN", "ALVER", "OSTERØY", "VAKSDAL",
    "SAMNANGER", "BJØRNAFJORDEN", "AUSTEVOLL",
}
TIER_2_BONUS = 10

# Relocation-worthiness penalty — user-requested 2026-08-29: within
# TIER_1_MUNICIPALS (or remote — no physical move needed either way), a
# short/part-time contract is a fine way to earn locally. Beyond that, it's
# only worth actually relocating for if the job pays like a real move —
# under 80% extent, or a Vikariat not proven to run at least a year, isn't
# ("все інше — це буде переїзд, а там вже треба нормальні гроші
# заробляти"). Two independent axes, checked in score_vacancy():
#   - RELOCATION_MIN_EXTENT_PERCENT: extent_percent below this = penalty.
#   - VIKARIAT_LONG_DURATION_RE: a Vikariat-family engagement_type not
#     matching this = penalty. Proximity-scoped to the word "vikariat"/
#     "engasjement"/"stilling"/"kontrakt" itself (not a bare "X år"
#     anywhere in the text, which mostly means "years of experience
#     required", not contract length — measured live 2026-08-29: a bare
#     "\d+ år" match hit "20 års erfaring" and similar nonsense 100% of the
#     time in a first pass). 93% of live Vikariat-type ads (1703/1837)
#     state no duration at all — unlike extent_percent (where "unknown"
#     stays neutral, the hard_blocks.py convention), an unstated Vikariat
#     duration defaults to "assume short" here: "vikariat" itself means
#     "temporary substitute" in Norwegian labor practice, and the stakes
#     are lower than a hard exclude — this is a reversible scoring
#     penalty, still visible via the score toggle, not an invisible block.
#     Measured against the full live corpus: 106 ads cleanly match as
#     >=12 months (0 false positives against "years of experience"
#     collisions), 28 cleanly state <12 months, the rest are unstated.
RELOCATION_MIN_EXTENT_PERCENT = 80
RELOCATION_PENALTY = -40
VIKARIAT_LONG_DURATION_RE = re.compile(
    r"(?:vikariat|engasjement|stilling(?:en)?|kontrakt)\D{0,20}(?:i |på |for )?"
    r"(?:(?:ett|1|12)\s*(?:-årig|års?)|1[2-9]\s*måneder|(?:1[2-9]|[2-9]\d)\s*måneders)|"
    r"(?:(?:ett|1|12)\s*(?:-årig|års?)|1[2-9]\s*måneder|(?:1[2-9]|[2-9]\d)\s*måneders)"
    r"\D{0,20}(?:vikariat|engasjement|stilling(?:en)?|kontrakt)"
)

DEGREE_REQUIRED_PATTERNS = [
    r"bachelorgrad (kreves|er (et )?krav)",
    r"mastergrad (kreves|er (et )?krav)",
    r"krever (en )?(relevant )?(bachelor|master)",
    r"høyere utdanning (kreves|er (et )?krav)",
    r"fagbrev (kreves|er (et )?krav)",
    r"krever fagbrev",
    # The "Dette må du ha for å være kvalifisert: ..." checklist header,
    # common in Jobbnorge/public-sector postings, wasn't caught by any
    # pattern above — verified live 2026-07-19 against 12 real active
    # postings, every one a genuine hard degree requirement (master i
    # rettsvitenskap, bachelor i regnskap og revisjon, etc.), no false
    # positives in the sample.
    r"kvalifisert:?\s{0,15}(master|bachelor)",
]

# Section-aware formal-qualification / programming-experience penalties —
# added 2026-08-29, round 2 of the flagged-queue audit. DEGREE_REQUIRED_PATTERNS
# above only catches a requirement phrased directly as "bachelorgrad
# kreves" — misses the far more common shape where the diploma/fagbrev
# sits as its own bullet under a "Kvalifikasjoner:"/"Requirements" heading
# with no verb of its own (same structural gap the truckførerbevis block
# was rewritten for). Reuses hard_blocks.py's shared clause/section
# machinery rather than reimplementing it (see that module's
# iter_requirement_clauses/REQUIREMENT_VERB_RE/OPTIONAL_MARKER_RE). A
# scoring PENALTY, not a hard_blocks.py exclusion — user-requested
# 2026-08-29: unlike truckførerbevis/forklift (a hard yes/no), a stated
# degree requirement is softer terrain (the user's own bachelor's, or
# relevant self-taught experience, can sometimes argue around it), so it
# demotes rather than hides.
FORMAL_QUALIFICATION_RE = re.compile(
    r"\bfagbrev\b|\bsvennebrev\b|\bfagskole\b|trade certificate|"
    r"competency certificate|vocational (?:diploma|certificate)|"
    r"\bbachelor|\bmastergrad|\bmaster i\b|høyere utdanning|høgare utdanning|"
    r"\bsivilingeniør|bachelor'?s degree|master'?s degree|\bbsc\b|\bmsc\b"
)
# The user's own bachelor's is in cybersecurity (recognized in Norway — see
# jobsearch-norway-profile memory) — a qualification requirement naming the
# IT field is one the user can actually point to, so it must not be
# penalized even though it's still a "formal qualification" in shape. Live
# case: "IT-driftstekniker" (59) explicitly lists "fagbrev, fagskole eller
# annen relevant høyere IT-utdanning" — the IT alternative IS what the user
# has.
FORMAL_QUALIFICATION_IT_FIELD_RE = re.compile(
    r"\bit\b|informatikk|datateknikk|datateknologi|informasjonsteknologi|"
    r"\bcyber|informasjonssikkerhet|informasjonssystem|computer science|\bsoftware\b"
)
FORMAL_QUALIFICATION_PENALTY = -35

# Catches roles that don't title themselves as a developer (dev_title_penalty
# above wouldn't fire) but still explicitly want hands-on coding experience —
# live case: "IT-rådgiver/IT-konsulent" (digital archivist role, re-flagged
# 2026-08-29) wants "erfaring med programmering og skripting (f.eks. Python,
# C#, JavaScript)" as a listed qualification.
PROGRAMMING_EXPERIENCE_RE = re.compile(
    r"erfaring med programmering|erfaring med (?:å )?(?:kode|skripting)|"
    r"programming experience|experience with programming"
)
PROGRAMMING_EXPERIENCE_PENALTY = -25


def _has_unmet_formal_qualification(text: str) -> bool:
    for clause, in_required_section in iter_requirement_clauses(text):
        if not FORMAL_QUALIFICATION_RE.search(clause):
            continue
        if FORMAL_QUALIFICATION_IT_FIELD_RE.search(clause):
            continue
        if has_optional_marker(clause):
            continue
        if REQUIREMENT_VERB_RE.search(clause) or in_required_section:
            return True
    return False


def _has_programming_experience_requirement(text: str) -> bool:
    for clause, _ in iter_requirement_clauses(text):
        if PROGRAMMING_EXPERIENCE_RE.search(clause) and not has_optional_marker(clause):
            return True
    return False


def _count_keyword_hits(text: str, keywords: list[str]) -> tuple[int, list[str]]:
    hits = [kw for kw in keywords if kw in text]
    return len(hits), hits


NEGATIONS = ("not ", "ikke ", "no ", "non-")


def _keyword_present_unnegated(text: str, keywords: list[str]) -> bool:
    """Plain substring matching can't tell "remote work available" from
    "on-site only (not remote)" — both contain "remote". Live false positive
    2026-07-17 (Sector Alarm listing, explicitly on-site). Checks the ~6
    characters immediately before each match for a negation word."""
    for kw in keywords:
        start = 0
        while (idx := text.find(kw, start)) != -1:
            before = text[max(0, idx - 6):idx]
            if not any(before.endswith(neg) for neg in NEGATIONS):
                return True
            start = idx + len(kw)
    return False


def _parse_occupation_category_level1(occupation_categories: str | None) -> set[str]:
    """`occupation_categories` is a JSON array like
    '[{"level1": "Industri og produksjon", "level2": "..."}, ...]' — NAV's
    own classification, stored verbatim by nav_client.py. Malformed/missing
    JSON (finn.no/LinkedIn rows never have this field) yields an empty set,
    not an error — this is an optional bonus signal, never a hard failure."""
    if not occupation_categories:
        return set()
    try:
        cats = json.loads(occupation_categories)
    except (ValueError, TypeError):
        return set()
    return {c.get("level1") for c in cats if isinstance(c, dict) and c.get("level1")}


def score_vacancy(
    title: str | None,
    description_html: str | None,
    municipal: str | None,
    county: str | None,
    language: str | None = None,
    occupation_categories: str | None = None,
    profile: str = "warehouse",
    extent_percent: int | None = None,
    engagement_type: str | None = None,
) -> tuple[int, dict]:
    title_l = (title or "").lower()
    text = f"{title_l} {strip_html(description_html)}".lower()
    breakdown = {}

    language_score = 10 if language == "en" else 0
    breakdown["language_bonus"] = {"points": language_score, "language": language}

    it_hits, it_kw = _count_keyword_hits(text, IT_SUPPORT_KEYWORDS)
    # "feilsøking" (troubleshooting) alone is too generic a word to mean
    # IT-support — measured live 2026-08-29: 139 active ads matched it as
    # the ONLY IT-support keyword present, almost all mechanical/industrial
    # titles (Testingeniør, Industrimekaniker, boat/train mechanics...).
    # Only counts when at least one other IT-support keyword is also there.
    # Same for its English twin, which was left ungated when feilsøking was
    # fixed — measured 2026-09-02: 7 active ads had "troubleshooting" as
    # their only IT-support keyword, and all 7 were electrical/mechanical
    # (Electricians ×2, Industrial Maintenance Mechanic, Sheet Metal
    # Workers, Hardware Test Engineer, power systems, a camper-rental
    # manager). Two of them were user-flagged the same day.
    if it_kw in (["feilsøking"], ["troubleshooting"], ["feilsøking", "troubleshooting"]):
        it_hits, it_kw = 0, []
    it_score = min(it_hits * 8, 40)
    breakdown["track_it_support"] = {"points": it_score, "matched": it_kw}

    # Two profiles, 2026-08-27 user-requested toggle: "warehouse" is the
    # 2026-08-18 retarget (production/склад/логистика, current default),
    # "it" restores IT-support as the dominant track by zeroing this
    # track's points — matched keywords still shown in the breakdown for
    # transparency, just worth 0 in this profile, not hidden. Retail is
    # NOT revived in "it" mode — that keyword removal was a separate,
    # permanent decision (see jobsearch-norway-profile memory), independent
    # of which profile is active.
    entry_hits, entry_kw = _count_keyword_hits(text, GENERAL_ENTRY_KEYWORDS)
    entry_title_hits = [kw for kw in GENERAL_ENTRY_TITLE_KEYWORDS if kw in title_l]
    entry_track_score = (
        min((entry_hits + len(entry_title_hits)) * 6, 30) if profile == "warehouse" else 0
    )
    breakdown["track_general_entry_level"] = {"points": entry_track_score, "matched": entry_kw + entry_title_hits}

    dev_hits, dev_kw = _count_keyword_hits(text, DEV_SECURITY_KEYWORDS)
    dev_score = min(dev_hits * 3, 15)
    breakdown["track_dev_security"] = {"points": dev_score, "matched": dev_kw}

    is_dev_title = any(kw in title_l for kw in DEV_TITLE_KEYWORDS)
    dev_title_penalty = DEV_TITLE_PENALTY if is_dev_title else 0
    breakdown["dev_title_penalty"] = {"points": dev_title_penalty, "matched": is_dev_title}

    is_entry_level = (
        any(kw in text for kw in ENTRY_LEVEL_PHRASES)
        or any(kw in title_l for kw in ENTRY_LEVEL_TITLE_KEYWORDS)
    )
    entry_level_bonus = 12 if is_entry_level else 0
    breakdown["entry_level_bonus"] = {"points": entry_level_bonus, "matched": is_entry_level}

    is_senior_title = any(kw in title_l for kw in SENIOR_TITLE_KEYWORDS)
    requires_years = any(re.search(p, text) for p in YEARS_EXPERIENCE_PATTERNS)
    senior_penalty = (-15 if is_senior_title else 0) + (-10 if requires_years else 0)
    breakdown["senior_penalty"] = {
        "points": senior_penalty,
        "matched": {"senior_title": is_senior_title, "years_required": requires_years},
    }

    management_matches = [
        p for p in MANAGEMENT_REQUIRED_PATTERNS if re.search(p, text)
    ]
    management_penalty = -30 if management_matches else 0
    breakdown["management_penalty"] = {
        "points": management_penalty,
        "matched": management_matches,
    }

    # "/hybrid)" title shorthand ("Java/Kotlin-utviklere (Oslo/Hybrid)") is
    # trusted unconditionally — titles are short and curated, unlike body
    # text nobody pads a title with an unrelated "hybrid" compound.
    is_remote = _keyword_present_unnegated(text, REMOTE_KEYWORDS) or "/hybrid)" in title_l
    remote_score = 15 if is_remote else 0
    breakdown["remote_bonus"] = {"points": remote_score, "matched": is_remote}

    municipal_u = (municipal or "").upper()
    county_u = (county or "").upper()
    if municipal_u in TIER_1_MUNICIPALS:
        location_score = 15
        location_reason = f"kommune {municipal}: Sogndal/Sogn-regionen"
    elif county_u in TIER_2_COUNTIES or municipal_u in TIER_2_MUNICIPALS:
        location_score = TIER_2_BONUS
        location_reason = f"{municipal or county}: Oslo/Bergen-regionen"
    elif county_u == "VESTLAND":
        location_score = 7
        location_reason = "fylke Vestland"
    else:
        location_score = 0
        location_reason = "utenfor Vestland"
    breakdown["location_bonus"] = {"points": location_score, "reason": location_reason}

    requires_car = _has_unmet_car_requirement(text)
    car_penalty = -20 if requires_car else 0
    breakdown["car_penalty"] = {"points": car_penalty, "matched": requires_car}

    requires_degree = any(re.search(p, text) for p in DEGREE_REQUIRED_PATTERNS)
    degree_penalty = -10 if requires_degree else 0
    breakdown["degree_penalty"] = {"points": degree_penalty, "matched": requires_degree}

    requires_norwegian_fluency = bool(
        re.search(
            r"flytende norsk|norsk (skriftlig og muntlig|muntlig og skriftlig)"
            r"|(written and oral|oral and written|verbal and written|spoken and written) "
            r"(communication )?(skills )?in norwegian"
            r"|fluent(ly)? in norwegian",
            text,
        )
    )
    language_penalty = -20 if requires_norwegian_fluency else 0
    breakdown["norwegian_fluency_penalty"] = {"points": language_penalty, "matched": requires_norwegian_fluency}

    # Same "it" profile carve-out as track_general_entry_level above: the
    # Industri og produksjon/Transport og lager BONUS is warehouse-specific
    # and zeroed in "it" mode, but the Salg og service/Reiseliv og mat
    # PENALTY always applies — rejecting retail/hospitality was a separate,
    # permanent decision, not tied to which track is currently favored.
    category_l1 = _parse_occupation_category_level1(occupation_categories)
    # Capped at the single-category value (15), not summed — a listing
    # tagged with both bonus categories (e.g. "Industri og produksjon" +
    # "Transport og lager") is still just one job, not doubly relevant.
    # Live case 2026-08-29: "Ekstrahjelp Skanem Bergen" got +30 from this
    # stacking alone, more than track_general_entry_level's own 30-point cap.
    category_bonus_raw = sum(OCCUPATION_CATEGORY_BONUS.get(c, 0) for c in category_l1)
    category_bonus = min(category_bonus_raw, 15) if profile == "warehouse" else 0
    category_penalty = sum(OCCUPATION_CATEGORY_PENALTY.get(c, 0) for c in category_l1)
    breakdown["occupation_category_bonus"] = {"points": category_bonus + category_penalty, "matched": sorted(category_l1)}

    is_phone_support = any(kw in text for kw in PHONE_SUPPORT_KEYWORDS)
    phone_penalty = -10 if is_phone_support else 0
    breakdown["phone_support_penalty"] = {"points": phone_penalty, "matched": is_phone_support}

    # See RELOCATION_MIN_EXTENT_PERCENT/VIKARIAT_LONG_DURATION_RE for the
    # full rationale. Waived inside TIER_1 (local, no relocation needed) and
    # for a remote/hjemmekontor role (is_remote, computed above — physical
    # municipal doesn't matter when the work itself isn't tied to it).
    needs_relocation = municipal_u not in TIER_1_MUNICIPALS and not is_remote
    low_extent = needs_relocation and extent_percent is not None and extent_percent < RELOCATION_MIN_EXTENT_PERCENT
    is_vikariat = (engagement_type or "").lower().startswith("vikariat")
    short_vikariat = needs_relocation and is_vikariat and not VIKARIAT_LONG_DURATION_RE.search(text)
    relocation_penalty = (RELOCATION_PENALTY if low_extent else 0) + (RELOCATION_PENALTY if short_vikariat else 0)
    breakdown["relocation_worthiness_penalty"] = {
        "points": relocation_penalty,
        "matched": {"low_extent": low_extent, "short_vikariat": short_vikariat},
    }

    requires_formal_qualification = _has_unmet_formal_qualification(text)
    formal_qualification_penalty = FORMAL_QUALIFICATION_PENALTY if requires_formal_qualification else 0
    breakdown["formal_qualification_penalty"] = {
        "points": formal_qualification_penalty, "matched": requires_formal_qualification,
    }

    requires_programming_experience = _has_programming_experience_requirement(text)
    programming_experience_penalty = PROGRAMMING_EXPERIENCE_PENALTY if requires_programming_experience else 0
    breakdown["programming_experience_penalty"] = {
        "points": programming_experience_penalty, "matched": requires_programming_experience,
    }

    base = 10
    total = base + sum(v["points"] for v in breakdown.values())
    total = max(0, min(100, total))
    breakdown["base"] = base
    breakdown["total"] = total

    return total, breakdown


# Only trusted when "kr"/"kr." directly precedes the figure — a bare 6-digit
# number alone is far too likely to be a phone number, org number, postal
# code, or reference ID. Verified live 2026-07-17: a loose \d{6} match hit
# 1164/1983 descriptions (almost all noise — dates, IDs); requiring the "kr"
# prefix narrowed that to 549/1983 genuine salary mentions, spot-checked.
SALARY_RE = re.compile(
    r"kr\.?\s?\d[\d\s.]{4,10}(?:\s?[-–]\s?\d[\d\s.]{4,10})?",
    re.I,
)

# "kr" as a SUFFIX instead of a prefix — "lønn mellom 380 000 og 520 000 kr",
# "årslønn 397 000 - 494 200 kr". Missed entirely by SALARY_RE above (live
# case, 2026-09-02: real "lønn mellom X og Y kr" text, salary badge blank).
# A bare number-then-kr suffix alone is just as noisy as the bare-number
# problem SALARY_RE already guards against — measured live: 211/~9800
# descriptions have one with no "kr"-prefix match, and most are referral
# bonuses/student-loan write-offs ("Hos oss får du 20 000 kr i sign-on"),
# not salary. Anchoring on a lønn/årslønn keyword within 40 chars before the
# figure narrows that to 85, spot-checked clean (one stray "vi avlønner med
# 10 000 kr pr veiledet student" — a mentoring stipend, not the job's own
# salary — accepted as noise at this rate).
SALARY_SUFFIX_RE = re.compile(
    r"(?:lønn\w*|årslønn)\b[^.]{0,40}?\d[\d\s.]{4,10}(?:\s?(?:og|til|[-–])\s?\d[\d\s.]{4,10})?\s?kr\b",
    re.I,
)


_SALARY_NUMBER_RE = re.compile(r"\d[\d\s.\xa0]*\d|\d")


def _format_salary(raw_match: str) -> str:
    """Numbers only, normalized to a single space thousand-separator, 'kr'
    prefix — regardless of which regex matched or how the source spaced/
    punctuated it (space, dot, nbsp all appear in the wild; the lønn-suffix
    match also drags along words like "lønn mellom ... og"). User feedback,
    2026-09-02: the badge is a compact list-row tag, the surrounding words
    are read-once noise there even when they're informative in the source
    text — collapse everything to what the badge actually needs."""
    nums = []
    for m in _SALARY_NUMBER_RE.findall(raw_match):
        digits = re.sub(r"[\s.\xa0]", "", m)
        if digits:
            nums.append(f"{int(digits):,}".replace(",", " "))
    if not nums:
        return raw_match.strip(" .-")
    return "kr " + " – ".join(nums[:2])


def _parse_salary(text: str | None) -> str | None:
    if not text:
        return None
    m = SALARY_RE.search(text)
    if not m:
        m = SALARY_SUFFIX_RE.search(text)
    if not m:
        return None
    return _format_salary(m.group(0))


_SALARY_FIGURE_RE = re.compile(r"\d[\d\s.]*")


def _salary_min_value(salary_text: str | None) -> int | None:
    """First figure in an already-matched salary_text (still has its "kr"
    prefix), thousand separators stripped, as a plain int for `>=` filtering.
    Not a normalized annual/monthly value — see the salary_min column
    comment in db.py for why that distinction matters here."""
    if not salary_text:
        return None
    m = _SALARY_FIGURE_RE.search(salary_text)
    if not m:
        return None
    digits = re.sub(r"[\s.]", "", m.group(0))
    return int(digits) if digits else None


_NORM_SUFFIX_RE = re.compile(r"\b(as|asa|kf|ans)\b\.?", re.I)
_NORM_STRIP_RE = re.compile(r"[^a-z0-9æøå ]")


def _dedup_key(business_name: str | None, title: str | None, municipal: str | None) -> tuple[str, str, str]:
    def norm(s):
        s = (s or "").lower()
        s = _NORM_SUFFIX_RE.sub("", s)
        s = _NORM_STRIP_RE.sub(" ", s)
        return re.sub(r"\s+", " ", s).strip()
    return (norm(business_name), norm(title), (municipal or "").strip().upper())


# A summary-only Jobbnorge row (~90-256 chars, before its own full-text
# backfill runs) isn't substantial enough to lend — same rough cutoff
# jobbnorge_client.py itself uses to detect "already enriched" (300) minus
# some margin, since a merely-decent summary is still much more signal
# than finn.no's bare title.
_LENDABLE_DESCRIPTION_MIN_LEN = 200


def _build_description_lender_lookup(rows) -> dict[tuple, tuple[str, str]]:
    """dedup_key -> (lender_uuid, description) for NAV/Jobbnorge rows with a
    substantial description — finn.no rows matching one on employer+title+
    municipal borrow its text for scoring, since finn.no's robots.txt rules
    out ever fetching a real description of its own (see finn_client.py).

    Matching on employer+title+municipal (not employer+municipal alone) is
    the whole point, not a nicety: a large employer (a kommune, a chain)
    posts many distinct jobs at once, so an employer-only match would
    confidently score a finn listing using a COMPLETELY UNRELATED job's
    text. Measured live 2026-07-18 on the real DB: employer+municipal alone
    over-counted matches at 19/60 finn rows; the correct employer+title+
    municipal key found the true, safe number — 11/60 (18%).

    Also used by linkedin rows (2026-08-09) — same shape of gap: LinkedIn's
    job-alert digest emails carry title/employer/location only, no
    description, and we deliberately don't fetch linkedin.com job pages to
    fill it in (ban risk to the user's real profile, see linkedin_client.py's
    own docstring)."""
    lookup: dict[tuple, tuple[str, str]] = {}
    for row in rows:
        if row["source"] not in ("nav", "jobbnorge"):
            continue
        description = row["description"]
        if not description or len(strip_html(description)) < _LENDABLE_DESCRIPTION_MIN_LEN:
            continue
        key = _dedup_key(row["business_name"], row["title"], row["municipal"])
        lookup[key] = (row["uuid"], description)
    return lookup


def rescore_one(conn, uuid: str) -> dict | None:
    """Same per-row scoring work as rescore_all()'s loop body, for a single
    row — used right after manually adding a vacancy (web/app.py's "Додати
    вакансію" form) so it gets an immediate score instead of sitting
    unscored until the next full sync. Deliberately skips the description-
    lending and cross-source-dedup passes that wrap the loop in
    rescore_all(): those are batch concerns (borrowing a description from a
    matching NAV/Jobbnorge row, deciding which physical duplicate "wins")
    that don't apply to a single freshly-added row in isolation — a manual
    add already carries whatever description the user typed in."""
    import db
    from hard_blocks import check_exclusion
    from jobbnorge_client import _parse_extent_percent

    row = db.get_vacancy(conn, uuid)
    if row is None:
        return None

    description = row["description"]
    language = row["language"]
    plain_description = strip_html(description)

    extent_pct = _parse_extent_percent(row["extent"], row["title"], plain_description)
    if extent_pct != row["extent_percent"]:
        db.set_extent_percent(conn, uuid, extent_pct)

    salary = _parse_salary(plain_description)
    salary_min = _salary_min_value(salary)
    if salary != row["salary_text"] or salary_min != row["salary_min"]:
        db.set_salary_text(conn, uuid, salary, salary_min)

    is_excluded, reason = check_exclusion(row["title"], plain_description, row["county"], extent_pct)
    db.set_exclusion(conn, uuid, is_excluded, reason)

    score, breakdown = score_vacancy(
        row["title"], description, row["municipal"], row["county"], language,
        row["occupation_categories"], profile="warehouse",
        extent_percent=extent_pct, engagement_type=row["engagement_type"],
    )
    db.set_score(conn, uuid, score, breakdown)

    score_it, breakdown_it = score_vacancy(
        row["title"], description, row["municipal"], row["county"], language,
        row["occupation_categories"], profile="it",
        extent_percent=extent_pct, engagement_type=row["engagement_type"],
    )
    db.set_score_it(conn, uuid, score_it, breakdown_it)

    return {"score": score, "score_it": score_it, "excluded": is_excluded, "exclusion_reason": reason}


def rescore_all(conn) -> dict:
    import db
    from hard_blocks import check_exclusion
    from jobbnorge_client import _parse_extent_percent

    rows = db.iter_scorable_vacancies(conn)
    lender_lookup = _build_description_lender_lookup(rows)
    excluded_count = 0
    all_candidates: list[dict] = []  # every scored row, for hard-block propagation + the dedup pass after
    for row in rows:
        description = row["description"]
        language = row["language"]

        # finn.no and linkedin rows never have a real description of their
        # own (see finn_client.py / linkedin_client.py) — borrow one from a
        # matching NAV/Jobbnorge listing if one exists. Skip when the
        # current description is neither empty nor a previous borrow — that
        # would mean something else (a future manual-entry feature) put
        # real text there, and this must never clobber it.
        if row["source"] in ("finn", "linkedin") and (row["description_borrowed_from"] or not description):
            key = _dedup_key(row["business_name"], row["title"], row["municipal"])
            lender_uuid, description = lender_lookup.get(key, (None, None))
            if description != row["description"] or lender_uuid != row["description_borrowed_from"]:
                # Only re-detect language when something actually changed —
                # code-review 2026-07-19 found this used to run
                # detect_language unconditionally on every pass (even
                # unchanged ones, duplicating set_borrowed_description's own
                # internal detection on the passes that did change).
                language = db.detect_language(description)
                db.set_borrowed_description(conn, row["uuid"], description, lender_uuid, language)

        plain_description = strip_html(description)

        # Recomputed for every row on every pass (both sources — NAV never
        # had this at all, jobbnorge only got it at sync time), unconditionally
        # — not "only if still NULL" — so a scoring-regex improvement takes
        # effect on the next rescore instead of needing a separate backfill.
        # Cheap: a regex match against text already in memory.
        extent_pct = _parse_extent_percent(row["extent"], row["title"], plain_description)
        if extent_pct != row["extent_percent"]:
            db.set_extent_percent(conn, row["uuid"], extent_pct)

        salary = _parse_salary(plain_description)
        salary_min = _salary_min_value(salary)
        # Also fires when only salary_min is stale (e.g. the column was just
        # added by migration and existing rows have salary_text but a NULL
        # salary_min) — not just when the text itself changed.
        if salary != row["salary_text"] or salary_min != row["salary_min"]:
            db.set_salary_text(conn, row["uuid"], salary, salary_min)

        is_excluded, reason = check_exclusion(row["title"], plain_description, row["county"], extent_pct)
        db.set_exclusion(conn, row["uuid"], is_excluded, reason)
        if is_excluded:
            excluded_count += 1

        score, breakdown = score_vacancy(
            row["title"], description, row["municipal"], row["county"], language,
            row["occupation_categories"], profile="warehouse",
            extent_percent=extent_pct, engagement_type=row["engagement_type"],
        )
        db.set_score(conn, row["uuid"], score, breakdown)

        # Second profile, stored separately (score_it/score_it_breakdown) —
        # the "IT-support like before the warehouse retarget" toggle
        # (2026-08-27). Dedup/hard-block decisions below stay keyed off the
        # warehouse score only — which physical row is the visible "keeper"
        # of a cross-source duplicate is a structural fact about the
        # vacancy, not something that should flip depending on which
        # profile you're currently viewing.
        score_it, breakdown_it = score_vacancy(
            row["title"], description, row["municipal"], row["county"], language,
            row["occupation_categories"], profile="it",
            extent_percent=extent_pct, engagement_type=row["engagement_type"],
        )
        db.set_score_it(conn, row["uuid"], score_it, breakdown_it)

        all_candidates.append({
            "uuid": row["uuid"], "score": score, "source": row["source"],
            "key": _dedup_key(row["business_name"], row["title"], row["municipal"]),
            "excluded": is_excluded, "reason": reason, "user_status": row["user_status"],
        })

    user_status_synced = _propagate_user_status_across_group(conn, all_candidates)
    excluded_count += _propagate_hard_blocks_across_group(conn, all_candidates)

    dedup_candidates = [c for c in all_candidates if not c["excluded"]]
    duplicates_excluded = _exclude_cross_source_duplicates(conn, dedup_candidates)
    excluded_count += duplicates_excluded

    return {"scored": len(rows), "excluded": excluded_count, "user_status_synced": user_status_synced}


def _propagate_user_status_across_group(conn, candidates: list[dict]) -> int:
    """user_status ('applied', 'interview', ...) is stored per-uuid, but the
    dedup tie-break below (highest score wins, ties broken by a fixed source
    order) can flip which twin of a cross-source duplicate is the visible
    one from one rescore_all() run to the next — same real-world posting,
    different uuid becomes the keeper. If the user had marked the
    now-hidden twin 'applied', the newly-visible one still reads 'new' and
    the tracked application looks lost — same failure shape as the
    2026-07-29 data-loss incident that motivated db.backup_db(). Once any
    copy in a dedup group carries a non-default status, every copy in the
    group gets it, so a flip can never surface a stale 'new'."""
    import db

    groups: dict[tuple, list[dict]] = {}
    for c in candidates:
        groups.setdefault(c["key"], []).append(c)

    synced = 0
    for group in groups.values():
        if len(group) < 2:
            continue
        non_default = sorted(
            (c for c in group if c["user_status"] != "new"),
            key=lambda c: c["uuid"],
        )
        if not non_default:
            continue
        target_status = non_default[0]["user_status"]
        for c in group:
            if c["user_status"] != target_status:
                db.set_user_status(conn, c["uuid"], target_status)
                c["user_status"] = target_status
                synced += 1
    return synced


def _propagate_hard_blocks_across_group(conn, candidates: list[dict]) -> int:
    """A hard block (sikkerhetsklarering, missing authorisation, etc.) is a
    fact about the real-world JOB, not about which source's scraped copy
    happened to contain matchable text. Live case (2026-08-10): a Politiets
    IT-enhet posting requiring sikkerhetsklarering was correctly blocked on
    its NAV/Jobbnorge copies (full description, regex matched), but its
    finn.no copy — same job, slightly different/shorter scraped text — did
    NOT match the clearance regex and stayed visible, unblocked, at 47%.
    Once one copy in a cross-source dedup group is hard-blocked for a real
    reason, every other copy in that group describes the same job and must
    be blocked too, before the (separate) highest-score-wins dedup pass
    below ever sees them."""
    import db

    groups: dict[tuple, list[dict]] = {}
    for c in candidates:
        groups.setdefault(c["key"], []).append(c)

    propagated = 0
    for group in groups.values():
        if len(group) < 2:
            continue
        block_reason = next((c["reason"] for c in group if c["excluded"] and c["reason"]), None)
        if not block_reason:
            continue
        for c in group:
            if not c["excluded"]:
                db.set_exclusion(conn, c["uuid"], True, block_reason)
                c["excluded"] = True
                c["reason"] = block_reason
                propagated += 1
    return propagated


# Tie-break order when two duplicate copies score identically (common —
# near-identical description text scores near-identically). Roughly
# data-completeness order: nav/jobbnorge/easycruit always carry a real own
# description, finn/linkedin never do (borrowed at best) — see
# jobsearch-norway-sources memory. Anything not listed sorts last.
# Explicit for every live source (2026-08-30, /fullreview deep Stage 4 —
# "manual"/"work.ua" existed in the DB but fell through to the .get()
# fallback of 99 implicitly) — both are hand-entered/low-volume sources,
# deliberately lowest priority: if the same posting also appears via an
# official feed, that copy should win the tiebreak as the visible keeper.
_SOURCE_TIE_BREAK_PRIORITY = {
    "nav": 0, "jobbnorge": 1, "easycruit": 2, "finn": 3, "linkedin": 4,
    "work.ua": 5, "manual": 6,
}


def _exclude_cross_source_duplicates(conn, candidates: list[dict]) -> int:
    """Same real-world posting appearing under more than one source (NAV +
    finn.no digest, so far — measured live 2026-07-17: 3 confirmed pairs
    out of ~2000 active rows, all TINE/ONEPARK/Gullfunn postings that hit
    both NAV's feed and a finn.no saved-search digest). Small enough in
    practice that a full merge-and-relink UI isn't worth building yet —
    reuses the existing excluded/exclusion_reason mechanism instead: keep
    the highest-scoring row of a group, flag the rest. Auditable and
    reversible via the same "show excluded" toggle hard_blocks already
    uses, not a silent delete.

    The sort key must be fully deterministic — score alone isn't, because
    ties are common and Python's stable sort then falls back to `group`'s
    build order, which mirrors iter_scorable_vacancies()'s unordered SELECT
    and isn't guaranteed stable across separate rescore_all() runs. Live
    bug (2026-08-15): with score-only sort, 4 duplicate pairs flipped which
    twin was excluded between two back-to-back rescore_all() runs on the
    same data — harmless that time (both twins were user_status='new'), but
    the flip itself is real: a status tracked on the twin that becomes
    hidden would otherwise look lost (see _propagate_user_status_across_group,
    the other half of this fix). source priority, then uuid, make the
    keeper choice reproducible even when score ties."""
    import db

    groups: dict[tuple, list[dict]] = {}
    for c in candidates:
        groups.setdefault(c["key"], []).append(c)

    excluded = 0
    for key, group in groups.items():
        if len(group) < 2 or len({c["source"] for c in group}) < 2:
            continue
        group.sort(key=lambda c: (
            -c["score"], _SOURCE_TIE_BREAK_PRIORITY.get(c["source"], 99), c["uuid"],
        ))
        keeper = group[0]
        for dupe in group[1:]:
            db.set_exclusion(
                conn, dupe["uuid"], True,
                f"Дублікат — та сама вакансія вже є на джерелі «{keeper['source']}»",
            )
            excluded += 1
    return excluded
