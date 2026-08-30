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
    r"\bfysioterapeut", r"\bergoterapeut",
    # jordmor/psykolog/farmasøyt/bioingeniør dropped their leading \b
    # 2026-08-30 (/fullreview deep, Stage 4) — same compound-word reasoning
    # as sykepleier above. Missed live: avdelingsjordmor, ultralydjordmor,
    # kommunepsykolog, provisorfarmasøyt, sykehusfarmasøyt,
    # produksjonsfarmasøyt, spesialbioingeniør, fagbioingeniør — checked
    # against the full live corpus (85 distinct titles across the four),
    # every match a genuine authorisation-gated role.
    r"jordmor", r"jordmødre",
    r"psykolog", r"farmasøyt", r"bioingeniør", r"\bradiograf",
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
    # stipendiat dropped its leading \b 2026-08-30 (/fullreview deep, Stage
    # 4) — "doktorgradsstipendiat" (literally "doctoral-degree stipend
    # position", an even more explicit PhD post than bare "stipendiat")
    # was missed on every one of dozens of live postings.
    r"stipendiat", r"\bpostdoktor", r"\bpostdoc",
    r"\bprofessor", r"\bførsteamanuensis", r"\bamanuensis",
    r"\bforsker\b", r"\bforskar\b", r"\bresearcher\b",
    r"\bph\.?d\b",
    # University/college-level "lektor" — added 2026-08-30 (/fullreview
    # deep, Stage 4). Deliberately here, not TEACHING_TITLE_PATTERNS's bare
    # \blektor: "universitetslektor"/"høgskolelektor" require a relevant
    # master's/PhD in the SUBJECT, not "godkjent lærerutdanning" (the K-12
    # pedagogical certification TEACHING_TITLE_PATTERNS is actually about)
    # — a real, not just cosmetic, distinction: unlike a K-12 lektor role,
    # these are gated on academic degree level, so blocking them under the
    # pedagogical-education reason would misattribute why they're
    # unreachable. 26 live titles measured, all genuine academic posts.
    r"universitetslektor", r"høgskolelektor", r"førstelektor",
    r"universitetslærer", r"høgskolelærer",
]

# Skilled trades gated behind a Norwegian fagbrev / certificate of
# apprenticeship. automatiker/industrimekaniker/instrumenttekniker/CNC-
# operatør/platearbeider/industrirørlegger added 2026-08-29 — measured
# against the live corpus (0 collisions with support/IT titles): these were
# already excluded from GENERAL_ENTRY_KEYWORDS as "itself a fagbrev-gated
# skilled trade" (see that list's own comment in scoring.py), but the
# matching hard_blocks title-block was never actually added until now.
TRADE_TITLE_PATTERNS = [
    # elektriker/rørlegger/tømrer/sveiser/mekaniker deliberately have NO
    # leading \b — same compound-word reasoning as sjåfør above and
    # HEALTH_TITLE_PATTERNS' sykepleier/lege entries. Found 2026-08-30
    # (/fullreview deep, Stage 4): serviceelektriker/industrielektriker,
    # anleggsrørlegger, aluminiumssveiser/plastsveiser, and a dozen
    # *mekaniker compounds (tungvognmekaniker, båtmekaniker,
    # lastebilmekaniker, bussmekaniker, motormekaniker, anleggsmekaniker...)
    # were all missed and still visible — checked against the live corpus,
    # every compound was a genuine fagbrev-gated trade variant, 0 false
    # positives. "industrimekaniker"/"industrimekanikar" below are now
    # redundant with bare "mekaniker" but left in place (harmless, and the
    # bare-word audit specifically confirmed them already).
    r"elektriker", r"elektrikar", r"rørlegger", r"røyrleggjar",
    r"tømrer", r"tømrar", r"sveiser", r"sveisar", r"mekaniker", r"mekanikar",
    # frisør KEEPS its leading \b — unlike the others above, its compound
    # false-positive is real: "hunde- og kattefrisør" (pet groomer) is a
    # different profession entirely, not the same hairdressing fagbrev.
    r"\bfrisør",
    # kranfører dropped its leading \b 2026-08-30 (/fullreview deep, Stage
    # 4) — "tårnkranfører" (tower-crane operator) was missed; checked, 0
    # false positives.
    r"\banleggsmaskinfører", r"kranfører",
    r"\bautomatiker", r"\bautomatikar", r"\bindustrimekaniker", r"\bindustrimekanikar",
    r"\binstrumenttekniker", r"\binstrumentation technician", r"\bcnc\b",
    r"\bplatearbeider", r"\bplatearbeidar", r"\bindustrirørlegger",
]

# Named engineering disciplines requiring a bachelor's/master's degree in
# that specific field — added 2026-08-29, user-flagged (Brunvoll
# "Elektroingeniører", Safe Bemanning "Maskiningeniører"). Deliberately NOT
# a bare "ingeniør" — measured live: that would also catch "Overingeniør —
# Brukerstøtte IT" (49) and "Overingeniør i Microsoft 365" (46), exactly
# the support-adjacent titles this profile is FOR. Only the named
# disciplines the user has no degree in.
ENGINEERING_TITLE_PATTERNS = [
    r"\belektroingeniør", r"\belkraftingeniør", r"\bmaskiningeniør",
    r"\bsivilingeniør", r"\bbygningsingeniør", r"\bkjemiingeniør",
    r"\bprosessingeniør", r"\bautomasjonsingeniør", r"\bkonstruksjonsingeniør",
    r"\bmechanical engineer\b", r"\bchemical engineer\b",
    r"\bcivil engineer\b", r"\bstructural engineer\b",
]

# Roles requiring driving/maritime certificates the user does not hold
# (no driving licence at all — see jobsearch-norway-profile memory).
# sjåfør/sjåfor deliberately have NO leading \b — same compound-word
# reasoning as HEALTH_TITLE_PATTERNS' sykepleier/lege entries above.
# Found 2026-08-30 (/fullreview deep, Stage 4): 46 live active,
# non-excluded titles were pure driver compounds the leading-\b version
# missed entirely — drosjesjåfør, taxisjåfør, betongbilsjåfør,
# varebilsjåfør, kranbilsjåfør, servicesjåfør, budbilsjåfør, and more —
# checked against the full live corpus, 0 false positives (every match
# was a genuine driving role).
LICENCE_TITLE_PATTERNS = [
    r"sjåfør", r"sjåfor",
    r"\bstyrmann", r"\boverstyrmann", r"\bmaskinist", r"\bskipsfører",
    # matros dropped its leading \b same pass — "lettmatros" (ordinary/
    # junior seaman) was missed; checked, 0 false positives.
    r"matros", r"\bkaptein", r"\bmaskinsjef",
]

# Regulated legal/finance professions.
LEGAL_FINANCE_TITLE_PATTERNS = [
    # advokat/jurist dropped their leading \b 2026-08-30 (/fullreview deep,
    # Stage 4) — "politiadvokat" (police prosecutor), "bistandsadvokat"
    # (victim's counsel), "arbeidsrettsjurist" (labor-law jurist),
    # "virksomhetsjurist" (in-house/corporate jurist) were all missed;
    # checked against the live corpus, every compound genuinely requires a
    # law degree/bar admission.
    r"advokat", r"jurist", r"\brevisor", r"\bregnskapsfører",
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
    ("ingeniorfag", ENGINEERING_TITLE_PATTERNS, "Потрібна вища освіта (bachelor/master) за конкретним інженерним фахом"),
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
# unconditional). Revisit/remove entirely if the user gets the certificate
# independently — see jobsearch-norway-profile memory for exactly how this
# behaved before the 2026-08-26 block was added (GENERAL_ENTRY_KEYWORDS-
# only, no block).
# --- Clause/section-aware requirement reading -------------------------------
# Norwegian ads structure requirements as a HEADING ("Kvalifikasjoner:")
# followed by bullet items, not one sentence — the verb that makes something
# mandatory ("må ha", "krav") often lives in the heading or a sibling bullet,
# not the same clause as the certificate name itself. A per-mention
# character-distance window (the pre-2026-08-29 approach) can't see that
# structure at all. This needs db.strip_html()'s block-tag-to-newline
# behavior (2026-08-29) to work — body_l here is expected to already have
# one bullet/paragraph per line.
# Widened 2026-08-29 (round 2 of the flagged-queue audit): English headings
# were missing entirely (ABB/CNC/Instrumentation Technician ads — "Your
# background:", "Requirements", "Education & Experience" — had NO heading
# recognized at all, so nothing was ever "in a required section"), and
# Norwegian headings didn't tolerate a prefix/compound form ("Relevante
# kvalifikasjoner", "Kvalifikasjoner og personlige egenskaper", "Dette må du
# ha for å lykkes i stillingen").
REQUIREMENT_HEADING_RE = re.compile(
    r"^(?:\w+\s+)?(?:kvalifikasjoner|kvalifikasjonar|kvalifikasjonskrav)"
    r"(?:\s+og\s+[\wæøå\s]+)?\s*[:–-]*$"
    r"|^(?:krav til (?:søker|deg)|kompetansekrav|formelle krav|vi krever|vi krev)\s*[:–-]*$"
    r"|^(?:du må ha|den som (?:ansettes|tilsettes) må ha|dette må du ha[\wæøå\s]*)\s*[:–-]*$"
    r"|^(?:i praksis betyr det at du har|vi ser etter deg som|vi søker deg som|"
    r"hvem ser vi etter|hva vi ser etter|hva ser vi etter|hvem er du|om deg)\s*[:–-]*$"
    r"|^(?:requirements?|qualifications?|your background|"
    r"education\s*(?:&|and)\s*experience|"
    r"what we(?:'re| are) looking for|what we expect|who you are|"
    r"required qualifications|minimum qualifications|"
    r"skills?\s*(?:&|and)\s*experience)\s*[:–-]*$"
)
OPTIONAL_HEADING_RE = re.compile(
    r"^(?:ønskede kvalifikasjoner|ønskelige kvalifikasjoner|ønskelig|"
    r"ønsket kompetanse|fordelaktig|det er en fordel(?:\s+om du har)?|vi ser gjerne|"
    r"personlige egenskaper|vi tilbyr|vi kan tilby|arbeidsoppgaver|"
    r"om stillingen|andre ønsker|fordeler)\s*[:–-]*$"
    r"|^(?:we offer|responsibilities|nice to have|preferred qualifications|benefits|"
    r"personal qualities|what we offer|desired qualifications)\s*[:–-]*$"
)
_CLAUSE_SPLIT_RE = re.compile(r"(?<![0-9])\.(?![0-9])|[;!?]")


def iter_requirement_clauses(body_l: str):
    """Yields (clause, in_required_section) for every clause (line split
    further into sentences) in body_l, tracking which requirement/optional
    heading — if any — the clause currently sits under. Shared by every
    check below AND by scoring.py's formal-qualification penalty — needs
    db.strip_html()'s block-tag-to-newline behavior (2026-08-29) to see
    bullet structure at all; body_l is expected to already have one
    bullet/paragraph per line."""
    section = None
    for line in body_l.split("\n"):
        line = line.strip()
        if not line:
            continue
        if REQUIREMENT_HEADING_RE.match(line):
            section = "req"
            continue
        if OPTIONAL_HEADING_RE.match(line):
            section = "opt"
            continue
        for clause in (p.strip() for p in _CLAUSE_SPLIT_RE.split(line)):
            if clause:
                yield clause, section == "req"


# Shared verb/softener vocabulary — used by every "is X actually a firm
# requirement" check in this file (truckfør, English forklift certificate)
# and imported by scoring.py for the formal-qualification/programming-
# experience penalties. Widened 2026-08-29 with English equivalents
# ("is required", "must have") — previously Norwegian-only, so an English
# ad stating "Valid forklift certificate T1–T4 is required" under a
# "Desired qualifications:" heading (itself an OPTIONAL heading) had no way
# to override that default; an explicit hard verb in the clause itself must
# always win regardless of which heading it sits under.
REQUIREMENT_VERB_RE = re.compile(
    r"må ha|må kunne|\bkrav\b|kreves|krever|krevast|\btrenger\b|"
    r"\bhar du\b|\bdu har\b|\bsom har\b|\bgyldig|innehar|"
    r"\bis required\b|\bare required\b|\bmust have\b|\bmust hold\b|\brequired\b"
)
# A softener anywhere in the clause wins even under a requirements heading
# ("Kvalifikasjoner: ... truckførerbevis er en fordel, men ikke et krav" —
# measured live, ~55 of 118 truckfør-mentioning ads use exactly this shape).
OPTIONAL_MARKER_RE = re.compile(
    r"gjerne|ønskelig|ønskjeleg|fordel|fordelaktig|pluss\b|positivt|"
    r"ikke\s+(?:\w+\s+)?krav|ikkje\s+(?:\w+\s+)?krav|ikke en forutsetning|"
    r"ikke noe must|bør ha|manglar du|mangler du|ikke nødvendig|kjekt om|"
    r"et ønske|kan veie opp|kan kompensere|"
    r"eller tilsvarende|eller tilsvarande|eller liknende|eller lignende|"
    r"eller realkompetanse|eller relevant erfaring|eller erfaring|eller lang erfaring|"
    r"an advantage|considered an advantage|is a plus|preferred\b|or equivalent|"
    r"nice to have|not required|desirable|training (?:can|will) be provided|we will train|"
    # Direct negation of the requirement verb itself ("trenger ikke X",
    # "krever ikke X") — added 2026-08-30 (/fullreview deep, Stage 4):
    # found via car_penalty's own new test ("Du trenger ikke førerkort for
    # denne stillingen" was scored as a hard requirement, since
    # REQUIREMENT_VERB_RE's bare `trenger`/`krever` matched with no
    # negation check at all). 381 live matches for this shape, not a rare
    # edge case.
    r"trenger ikke|trengs ikke|krever ikke|kreves ikke"
)
_PARENS_RE = re.compile(r"\([^)]*\)")


def _has_unmet_requirement(mention_re, clauses, training_re=None, title_l=None):
    """Shared verdict logic for "does `mention_re` show up as a firm, unmet
    requirement anywhere in `clauses`, with the title as a structural
    fallback". `training_re`, if given, cancels a mention the same way the
    truckførerbevis training-offered override works — checked in the
    mention's own clause and the next one (a trailing clause, in every real
    example seen: "...er ønskelig. opplæring kan gis")."""
    mention_indices = [i for i, (c, _) in enumerate(clauses) if mention_re.search(c)]

    def _training_offered_near(i: int) -> bool:
        if training_re is None:
            return False
        nxt = clauses[i + 1][0] if i + 1 < len(clauses) else ""
        return bool(training_re.search(clauses[i][0]) or training_re.search(nxt))

    for i in mention_indices:
        clause, in_required_section = clauses[i]
        if _training_offered_near(i):
            continue
        # Scoped OUTSIDE parentheses: "Truckførerbevis T8 (T8.4 er en
        # fordel men ikke et krav)" means T8 itself IS required — only the
        # more advanced T8.4 sub-class is optional (live case: CargoNet,
        # user-flagged 2026-08-29).
        if OPTIONAL_MARKER_RE.search(_PARENS_RE.sub(" ", clause)):
            continue
        if REQUIREMENT_VERB_RE.search(clause) or in_required_section:
            return True

    if title_l is not None and mention_re.search(title_l):
        # Training-offered override only counts when it sits near an actual
        # mention in the body (same adjacency rule as above) — a training
        # sentence anywhere else in the body must not save a title-driven
        # block either (2026-08-26 bug class: CargoNet's "T4 erfaring" case,
        # an unrelated "Opplæring vil bli gitt" onboarding sentence several
        # clauses away otherwise silently overrode a real requirement).
        if any(_training_offered_near(i) for i in mention_indices):
            return False
        return True

    return False


TRUCKFORERBEVIS_MENTION_RE = re.compile(r"truckfø")
TRUCKFORERBEVIS_TRAINING_OFFERED_RE = re.compile(
    r"opplæring (vil bli gitt|kan gis|gis)|vi lærer deg opp|får opplæring|læres opp"
)


def _has_unmet_truckforerbevis_requirement(title_l: str, body_l: str) -> bool:
    """True when truckførerbevis reads as a firm requirement with no
    on-the-job training offered *for that certificate specifically*.

    Rewritten 2026-08-29 (user spot-checked the queue again and found the
    2026-08-26 per-mention-window version still missed real requirements
    like CargoNet's — "Kvalifikasjoner: Lasting/lossing ... (truckførerbevis
    T8) ... Truckførerbevis T8 (T8.4 er en fordel men ikke et krav)": the
    mandatory framing is the "Kvalifikasjoner:" HEADING two bullets above,
    the certificate's own clause has no verb at all. Section-aware analysis
    (this version) catches this — a bullet under a requirements heading with
    no softener of its own counts as required even without its own verb.
    Measured against the full live corpus (118 truckfør-mentioning ads,
    2026-08-29): old rule blocked 21, this one blocks 51, with exactly 1
    acceptable regression (an ambiguous "krav" heading whose own bullet list
    mixed hard and soft items in a shape too tangled to split further)."""
    clauses = list(iter_requirement_clauses(body_l))
    return _has_unmet_requirement(
        TRUCKFORERBEVIS_MENTION_RE, clauses,
        training_re=TRUCKFORERBEVIS_TRAINING_OFFERED_RE, title_l=title_l,
    )


# English equivalent of the truckførerbevis check, added 2026-08-29 (live
# case, user-flagged: "Warehouse workers with forklift certificate" — NAV's
# feed carries plenty of English-language ads from staffing agencies).
# Norwegian "truckførerbevis" and English "forklift certificate" are kept as
# two separate mention patterns rather than merged into one regex — the
# words share no substring, and a merged pattern would just be harder to
# read for no benefit.
FORKLIFT_CERT_MENTION_RE = re.compile(r"forklift (?:licen[cs]e|certificate|cert\b)")


def _has_unmet_forklift_certificate_requirement(title_l: str, body_l: str) -> bool:
    clauses = list(iter_requirement_clauses(body_l))
    return _has_unmet_requirement(FORKLIFT_CERT_MENTION_RE, clauses, title_l=title_l)


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

    if _has_unmet_forklift_certificate_requirement(title_l, body_l):
        return True, "Вимагає forklift certificate без навчання на місці — поки не отримуємо"

    return False, None
