"""Hard exclusions — vacancies the user legally cannot be hired for.

This is deliberately separate from scoring.py. Scoring answers "how well does
this fit?" on a sliding scale; this module answers "is this even possible?"
as a yes/no. A profession requiring a Norwegian healthcare authorisation or a
fagbrev the user does not hold is not a low-scoring match — it is a zero,
and no amount of location/language bonus should ever float it up the list.

Design decisions (agreed with the user 2026-07-17, "вони нам не треба,
потрапити туди 0 шансів"):

- Excluded rows are FLAGGED, never deleted. The UI hides them by default but
  keeps a toggle + count, so a wrong rule is visible and correctable rather
  than silently eating good vacancies.
- Blocking keys off the TITLE, not the body. A cleaning or kitchen job at a
  sykehjem legitimately mentions "sykehjem"/"sykepleier" in its description;
  only the job's own title reliably says what the job IS. The one body-level
  exception is an explicit authorisation requirement phrase.
- These are Norwegian *statutory* requirements (helsepersonelloven for health
  professions, formal pedagogical qualification for teaching posts, academic
  degrees for research posts) — not employer preferences that a good cover
  letter could argue around.
"""

import re

# Health professions requiring authorisation under helsepersonelloven.
# Norwegian authorisation requires a recognised education; the user's diploma
# is unrecognised and in an unrelated field, so these are unreachable.
HEALTH_TITLE_PATTERNS = [
    # No leading \b on these: Norwegian compounds them into single words
    # (operasjonssykepleier, spesialsykepleier, intensivsykepleier), so a
    # word-boundary anchor would miss the exact roles most gated behind
    # authorisation.
    r"sykepleier", r"sjukepleiar", r"sykepleiar",
    r"helsefagarbeider", r"helsefagarbeidar",
    r"vernepleier", r"vernepleiar",
    r"\blege\b", r"\blegar\b", r"\boverlege\b",
    # Doctor-role COMPOUNDS ("fastlege", "kommuneoverlege") need the same
    # no-leading-\b treatment as sykepleier above, for the same reason —
    # but unlike "sykepleier" (11 letters, safe as a bare substring), bare
    # "lege" (4 letters) collides with unrelated words containing it as a
    # substring, not a suffix: "legesenter"/"legekontor"/"legeutdanning"
    # (workplace names, not the job's own title), "Leger Uten Grenser"
    # (an employer name), "samfunnsvitskaplege" (coincidental — Nynorsk for
    # "social-science", nothing to do with medicine). So this is an
    # explicit whitelist of the actual doctor-role compounds seen live
    # (2026-07-19, flagged: "Kommuneoverlege og fastlege" scored 16% and
    # slipped through), not a blanket bare "lege".
    r"fastlege", r"tilsynslege", r"fylkeslege", r"kommunelege",
    r"kommuneoverlege", r"sykehjemslege", r"distriktslege",
    r"allmennlege", r"turnuslege",
    r"\btannlege", r"\btannpleier", r"\btannhelsesekret",
    r"\bfysioterapeut", r"\bergoterapeut", r"\bjordmor", r"\bjordmødre",
    r"\bpsykolog", r"\bfarmasøyt", r"\bbioingeniør", r"\bradiograf",
    r"\bambulanse", r"\bparamedic", r"\boptiker", r"\bkiropraktor",
    r"\bhelsesykepleier", r"\bhelsesjukepleiar", r"\bmiljøterapeut",
    r"\bsosionom", r"\bbarnevernspedagog", r"\bhjelpepleier",
]

# Teaching / pedagogical posts requiring formal Norwegian pedagogical
# qualification (godkjent lærerutdanning).
TEACHING_TITLE_PATTERNS = [
    r"\blærer\b", r"\blærar\b", r"\blærere\b", r"\blærarar\b",
    r"\blærervikar", r"\blærarvikar",
    r"\badjunkt", r"\blektor",
    r"\bbarnehagelærer", r"\bbarnehagelærar",
    r"\bpedagogisk leder", r"\bpedagogisk leiar",
    r"\bpedagog\b", r"\bspesialpedagog",
]

# Academic posts requiring a PhD or at least a master's degree.
ACADEMIC_TITLE_PATTERNS = [
    r"\bstipendiat", r"\bpostdoktor", r"\bpostdoc",
    r"\bprofessor", r"\bførsteamanuensis", r"\bamanuensis",
    r"\bforsker\b", r"\bforskar\b", r"\bresearcher\b",
    r"\bph\.?d\b",
]

# Skilled trades gated behind a Norwegian fagbrev / certificate of
# apprenticeship.
TRADE_TITLE_PATTERNS = [
    r"\belektriker", r"\belektrikar", r"\brørlegger", r"\brøyrleggjar",
    r"\btømrer", r"\btømrar", r"\bsveiser", r"\bsveisar",
    r"\bfrisør", r"\bbilmekaniker", r"\bmekanikar",
    r"\banleggsmaskinfører", r"\bkranfører",
]

# Roles requiring driving/maritime certificates the user does not hold
# (no driving licence at all — see jobsearch-norway-profile memory).
LICENCE_TITLE_PATTERNS = [
    r"\bsjåfør", r"\bsjåfor", r"\bbussjåfør", r"\blastebilsjåfør",
    r"\bstyrmann", r"\boverstyrmann", r"\bmaskinist", r"\bskipsfører",
    r"\bmatros", r"\bkaptein", r"\bmaskinsjef",
]

# Regulated legal/finance professions.
LEGAL_FINANCE_TITLE_PATTERNS = [
    r"\badvokat", r"\bjurist", r"\brevisor", r"\bregnskapsfører",
    r"\brekneskapsførar",
    # "Juridisk rådgiver" (legal advisor) — same law-degree requirement as
    # "jurist" but a different word (adjective, not the noun "jurist"), so
    # it slipped past the pattern above (live 2026-07-19, "Juridisk
    # rådgiver" scored 21%, description explicitly requires "master i
    # rettsvitenskap/cand.jur"). Deliberately the phrase, not bare
    # "juridisk" — that alone false-matches "Det juridiske fakultet"
    # (workplace name) and "juridiske fag" (subject-matter descriptor on a
    # librarian role), neither of which requires a law degree themselves.
    r"juridisk rådgiver", r"juridiske rådgivere",
    # "rådgiver juridisk" (reversed word order, e.g. "Rådgiver juridisk
    # (vikariat)") also needs the negative lookahead — without it, this
    # matches the SAME false-positive shape the comment above warns about,
    # just from the other side: "Rådgiver juridisk seksjon"/"avdeling" is a
    # generalist/administrative advisor merely attached to a legal
    # department, not a lawyer role, and doesn't require a law degree
    # (code-review 2026-07-19).
    r"rådgiver juridisk(?!\s*(seksjon|avdeling|fakultet))",
]

# Sworn Norwegian police officer ranks — require a 3-year Politihøgskolen
# bachelor's, a specific non-transferable credential, same category as
# health/teaching authorisation. Deliberately NOT a bare "politi" (would
# false-match "KI-politikk"/"utlendingspolitikk" — policy, unrelated word
# containing "politi" as a substring) and NOT "etterforsker"/"avsnittsleder"
# alone (Norway does have civilian investigator/section-lead roles at
# politiet that don't require the police academy — too ambiguous to block
# by title alone). Only the ranks themselves, confirmed live 2026-07-19
# ("Politibetjent 3/2/1" scored 10% and slipped through unblocked).
POLICE_TITLE_PATTERNS = [
    r"politibetjent", r"politioverbetjent", r"politiførstebetjent", r"politiinspektør",
]

# Norwegian apprenticeships (lærling/læreplass) — NOT an entry-level path for
# this profile. A lærling position requires completed Vg1+Vg2 videregående
# skole in the specific trade first (see live example: "Completed and passed
# Vg2 child and youth worker subject" as a hard requirement). This was
# previously scored as an entry-level BONUS in scoring.py, which was
# backwards — fixed 2026-07-17 alongside this block category.
APPRENTICESHIP_TITLE_PATTERNS = [r"lærling", r"læreplass"]

BLOCK_CATEGORIES = [
    ("helseautorisasjon", HEALTH_TITLE_PATTERNS, "Потрібна норвезька авторизація медпрацівника"),
    ("pedagogisk utdanning", TEACHING_TITLE_PATTERNS, "Потрібна норвезька педагогічна освіта"),
    ("akademisk grad", ACADEMIC_TITLE_PATTERNS, "Потрібен PhD / магістр (академічна позиція)"),
    ("fagbrev", TRADE_TITLE_PATTERNS, "Потрібен норвезький fagbrev"),
    ("sertifikat", LICENCE_TITLE_PATTERNS, "Потрібні права / морський сертифікат"),
    ("autorisert yrke", LEGAL_FINANCE_TITLE_PATTERNS, "Регульована професія (право/аудит)"),
    ("laerling", APPRENTICESHIP_TITLE_PATTERNS, "Потрібна завершена Vg1/Vg2 videregående (учнівство)"),
    ("politi", POLICE_TITLE_PATTERNS, "Потрібна освіта Politihøgskolen (норвезька поліцейська академія)"),
]

# The one body-level check: an explicit statement that authorisation is
# required *of the position itself*. Phrased tightly enough that it won't fire
# on "vi er autorisert lærebedrift" (employer self-description) nor on
# "norsk autorisasjon kreves for søkere som er sykepleiere..." — a conditional
# clause that means "IF you're a nurse you need authorisation", on a posting
# that also welcomes assistants who don't. That conditional shape was a live
# false positive (Otium bo- og velferdssenter tilkallingsvikar, 2026-07-17):
# the ad explicitly "søker etter assistenter og helsepersonell" and offers
# "god opplæring". The negative lookahead below drops any match immediately
# followed by "for søkere"/"for deg som".
BODY_AUTHORISATION_PATTERNS = [
    # "autorisasjon som sykepleier" as a listed qualification — the position is
    # FOR an authorised professional. Matches both "du har norsk autorisasjon
    # som sykepleier" and a bare bullet "autorisasjon som sykepleier".
    r"autorisasjon som (sykepleier|sjukepleiar|helsefagarbeider|vernepleier|lege|fysioterapeut)",
    # Generic "authorisation required" — but NOT the conditional
    # "autorisasjon kreves for søkere som er sykepleiere..." shape, which only
    # requires it IF you happen to be a nurse, on a posting also open to
    # assistants (live false positive 2026-07-17, Otium tilkallingsvikar).
    r"krever (norsk )?autorisasjon(?! for søkere)(?! for deg som)",
    r"må ha (norsk )?autorisasjon(?! for søkere)(?! for deg som)",
    r"godkjent autorisasjon fra helsedirektoratet",
]

# Norwegian government/defense security clearance (sikkerhetsklarering) —
# requires Norwegian citizenship in practice (sikkerhetsloven), unreachable
# for someone without protection status yet, let alone citizenship. Reported
# live 2026-07-18 (Forsvaret cyber-defense posting: "Du må kunne
# sikkerhetsklareres til HEMMELIG og NATO SECRET før tiltredelse"). Body-level
# like BODY_AUTHORISATION_PATTERNS, not title-level — defense/police/security
# job titles rarely say "clearance" in the title itself, it's stated as a
# requirement in the body.
# The second half of this pattern (autorisasjon-phrasing) was added
# 2026-08-15 after Brønnøysundregistrene's "Vi søker systemutviklere"
# (jobbnorge-305037) scored 46 and sat unblocked in the list. It demands
# "Du må kunne autoriseres for BEGRENSET etter sikkerhetsloven" and spells
# out the disqualifier directly: "Er du utenlandsk statsborger, skal
# autorisasjonsansvarlig hos oss vurdere om din tilknytning til hjemlandet
# og hjemlandets sikkerhetsmessige betydning utgjør en risiko" — on national
# registries that include våpenregisteret. Same category as the 2026-07-18
# addition, same law, just the authorisation wording instead of the
# clearance wording, so the original regex never saw it. Widening it caught
# 28 more live ads (Sjøforsvaret, three politidistrikt, Kartverket, NAV
# Teknologi, a second Brønnøysundregistrene posting) with no false positives
# in the audit — the "enkelte stillinger" guard below covers both halves.
SECURITY_CLEARANCE_RE = re.compile(
    r"(må kunne sikkerhetsklareres|krav(?:er)? (?:om|til) sikkerhetsklarering|"
    r"vilkår for sikkerhetsklarering|kreve(?:r)? sikkerhetsklarering|"
    r"autoriseres for (?:begrenset|konfidensielt|hemmelig|strengt hemmelig)|"
    r"klareres for (?:begrenset|konfidensielt|hemmelig|strengt hemmelig)|"
    r"autorisasjon etter sikkerhetsloven|autoriseres etter sikkerhetsloven)"
)


def _has_definite_security_clearance_requirement(text: str) -> bool:
    """Live false-positive risk found auditing 164 real matches (2026-07-18):
    24 of them were the generic disclaimer "enkelte stillinger vil kunne
    kreve sikkerhetsklarering" ("SOME positions [in our organization] may
    require clearance") on completely unrelated postings (Tannpleier,
    Arealplanlegger, Prosjektledere at a fylkeskommune) — boilerplate about
    the employer at large, not a requirement of THIS job. Only counts a
    match as definite when "enkelte stillinger" doesn't appear shortly
    before it."""
    for m in SECURITY_CLEARANCE_RE.finditer(text):
        before = text[max(0, m.start() - 60):m.start()]
        if "enkelte stillinger" not in before:
            return True
    return False


# International relocation-recruitment ads — BPO/customer-service agencies
# recruiting Norwegian speakers to work FROM another country entirely, not
# Norway. These slip into NAV's feed despite the job being physically
# abroad. "EU passport" as a stated requirement is the narrow, reliable
# tell: a Norway-based employer never phrases a requirement this way —
# Norway itself isn't in the EU, so a domestic posting asks about the
# right to work in Norway, not an EU passport specifically. Live case
# 2026-08-15, user-flagged: "Norwegian speaker? Kick-start your
# international career in Greece!" (Jobs By Nordics AB) — the job is
# physically in Athens/Thessaloniki, scored 47% and got a false +15
# remote_bonus from "100% remote within Greece" matching the generic
# "100% remote" phrase. Checked against the live corpus: this exact
# phrase appears in exactly 1 of ~5000 active, non-excluded vacancies —
# this one.
EU_PASSPORT_REQUIREMENT_RE = re.compile(r"eu[\s-]?passport", re.I)


# Truckførerbevis (forklift certificate) — added 2026-08-26 at the user's
# explicit request, and deliberately temporary/conditional, unlike every
# other block above. Unlike a driving licence, truckførerbevis IS
# genuinely achievable — a T1-T5 course needs no prior licence or fagbrev,
# just 5-7.5k kr and a day or two (see jobsearch-norway-profile memory).
# But the user isn't pursuing it independently right now, so a posting
# that firmly REQUIRES one is a real disqualifier today — UNLESS the ad
# itself offers to train the hire on the job, which the user is fine with
# ("якщо на місці вже запропонують, то я не проти"). This is the first
# *conditional* body-level block in this file (every other check here is
# unconditional) — narrow on purpose: measured against the live corpus
# (2026-08-26, ~112 truckfør*-vacancies), the overwhelming majority use
# soft/optional phrasing ("er en fordel, men ikke et krav", "gjerne",
# "ønskelig", "bør ha") that must NOT be blocked, and only a handful use an
# unambiguous hard-requirement verb ("må ha truckførerbevis", "dette er et
# krav", "kreves"). Erring toward under-blocking the ambiguous middle
# ("at du har X" bullet lists with no visible verb) is deliberate — same
# "don't hide what we can't confidently judge" principle as the low-extent
# check below. Revisit/remove entirely if the user gets the certificate
# independently — see jobsearch-norway-profile memory for exactly how this
# behaved before this change (GENERAL_ENTRY_KEYWORDS-only, no block).
TRUCKFORERBEVIS_MENTION_RE = re.compile(r"truckfø")
TRUCKFORERBEVIS_HARD_REQUIREMENT_RE = re.compile(
    r"må ha|må kunne|\ber et krav\b|dette er et krav|kreves|krever"
)
TRUCKFORERBEVIS_TRAINING_OFFERED_RE = re.compile(
    r"opplæring (vil bli gitt|kan gis|gis)|vi lærer deg opp|får opplæring|læres opp"
)


def _has_unmet_truckforerbevis_requirement(title_l: str, body_l: str) -> bool:
    """True when truckførerbevis reads as a firm requirement with no
    on-the-job training offered *for that certificate specifically*.

    Live bug found 2026-08-26 (user spot-checked the "training offered"
    list and couldn't find any training mention on 2 of the first 3): the
    training-offered check originally searched the WHOLE body, so a
    generic "Full opplæring vil bli gitt" onboarding sentence — unrelated
    to truckførerbevis, often nowhere near it — silently overrode a real
    requirement. Live case: "Truckfører med T4 erfaring" lists
    "Truckførerbevis T1–T4" under Kvalifikasjoner (candidates must already
    hold it), then a generic training sentence 276 characters later talks
    about general onboarding, not the certificate — the old code let it
    through anyway. Fixed by requiring the training phrase to sit within
    ~50 chars of a truckfør mention, same window as the hard-requirement
    check — measured against the one confirmed on-topic live case
    ("truckførerbevis klasse t1 er ønskelig. opplæring kan gis", 39 chars
    apart) vs. the false-override case above (276 chars), 50 cleanly
    separates them.

    Checks the title as a role-defining term ("Truckfører søkes")
    unconditionally — that role structurally needs the certificate to do
    the job at all — but a body mention with training offered nearby still
    overrides even a title-driven block."""
    body_mentions = list(TRUCKFORERBEVIS_MENTION_RE.finditer(body_l))

    def _training_offered_near(pos: int) -> bool:
        # Asymmetric: training phrasing is a trailing clause in every real
        # example seen ("...er ønskelig. opplæring kan gis"), so the window
        # extends further forward than back — 90 chars comfortably fits a
        # full "opplæring vil bli gitt"-length clause after "truckfø" (a
        # 50/50 symmetric window clipped "gitt" off a real phrase in
        # testing) while staying nowhere near the 276-char distance of the
        # unrelated onboarding sentence this fix excludes.
        window = body_l[max(0, pos - 50):pos + 90]
        return bool(TRUCKFORERBEVIS_TRAINING_OFFERED_RE.search(window))

    for m in body_mentions:
        window = body_l[max(0, m.start() - 50):m.end() + 50]
        if TRUCKFORERBEVIS_HARD_REQUIREMENT_RE.search(window) and not _training_offered_near(m.start()):
            return True

    if TRUCKFORERBEVIS_MENTION_RE.search(title_l):
        if any(_training_offered_near(m.start()) for m in body_mentions):
            return False
        return True

    return False


# Below this and outside Vestland, relocating doesn't cover rent — see
# PLAN.md point 4 ("щоб при переїзді можна було реально зняти хату/кімнату/
# купити їжи"). Only applied when extent_percent is actually known (parsed
# from title/description/jobScope) — an unresolved percentage is NOT treated
# as failing this check, per the user's own steer toward "не вгадаєш" (don't
# hide what we can't confidently judge).
LOW_EXTENT_FAR_THRESHOLD = 60


def check_exclusion(
    title: str | None,
    description_text: str | None,
    county: str | None = None,
    extent_percent: int | None = None,
) -> tuple[bool, str | None]:
    """Returns (is_excluded, human-readable reason in Ukrainian)."""
    title_l = (title or "").lower()

    for _key, patterns, reason in BLOCK_CATEGORIES:
        for pattern in patterns:
            if re.search(pattern, title_l):
                return True, reason

    if (
        county
        and county.strip().upper() != "VESTLAND"
        and extent_percent is not None
        and extent_percent < LOW_EXTENT_FAR_THRESHOLD
    ):
        return True, f"Поза Vestland і лише {extent_percent}% ставки — переїзд економічно нереальний"

    body_l = (description_text or "").lower()
    for pattern in BODY_AUTHORISATION_PATTERNS:
        if re.search(pattern, body_l):
            return True, "В описі прямо вимагається норвезька авторизація"

    if _has_definite_security_clearance_requirement(body_l):
        return True, "Потрібен допуск до державної таємниці (sikkerhetsklarering) — недосяжно без громадянства"

    if EU_PASSPORT_REQUIREMENT_RE.search(body_l):
        return True, "Вакансія фізично за кордоном (вимагає EU passport), не в Норвегії"

    if _has_unmet_truckforerbevis_requirement(title_l, body_l):
        return True, "Вимагає truckførerbevis без навчання на місці — поки не отримуємо"

    return False, None
