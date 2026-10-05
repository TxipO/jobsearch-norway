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
import unicodedata
from bisect import bisect_right

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
    # (?!assistent): "Tannlegeassistent" is unauthorised support work
    # (2026-10-05, /fullreview deep) — unlike tannpleier/tannhelsesekretær.
    r"\btannlege(?!assistent)", r"\btannpleier", r"\btannhelsesekret",
    r"\bfysioterapeut", r"\bergoterapeut",
    # jordmor/psykolog/farmasøyt/bioingeniør dropped their leading \b
    # 2026-08-30 (/fullreview deep, Stage 4) — same compound-word reasoning
    # as sykepleier above. Missed live: avdelingsjordmor, ultralydjordmor,
    # kommunepsykolog, provisorfarmasøyt, sykehusfarmasøyt,
    # produksjonsfarmasøyt, spesialbioingeniør, fagbioingeniør — checked
    # against the full live corpus (85 distinct titles across the four),
    # every match a genuine authorisation-gated role.
    r"jordmor", r"jordmødre",
    # 2026-10-05 (/fullreview deep): "psykolog(?!i)" — "Studiekonsulent ved
    # Institutt for psykologi" is an admin job at a department, the subject
    # noun "psykologi" is not the profession. "farmasøyt(?!isk)" — the
    # adjective "farmasøytisk" in "Produksjonsoperatør farmasøytisk
    # industri" / "Lagermedarbeider farmasøytisk grossist" names the
    # employer's sector (warehouse/production = wanted jobs), not an
    # authorised pharmacist. "ambulanse(?!stasjon)" — "Renholder ved
    # ambulansestasjonen" is a cleaner at a workplace. Compounds like
    # kommunepsykolog/provisorfarmasøyt are untouched (the lookaheads only
    # fire on the exact false-block suffixes).
    r"psykolog(?!i)", r"farmasøyt(?!isk)", r"bioingeniør", r"\bradiograf",
    r"\bambulanse(?!stasjon)", r"\bparamedic", r"\boptiker", r"\bkiropraktor",
    r"\bhelsesykepleier", r"\bhelsesjukepleiar", r"\bmiljøterapeut",
    r"\bsosionom", r"\bbarnevernspedagog", r"\bhjelpepleier",
]

# Teaching / pedagogical posts requiring formal Norwegian pedagogical
# qualification (godkjent lærerutdanning).
TEACHING_TITLE_PATTERNS = [
    # "lærer" is also the VERB ("Vi lærer deg opp som lagermedarbeider" —
    # 2026-10-05, /fullreview deep): skip it when a pronoun subject precedes
    # and deg/dem/opp/bort follows. Deliberately NOT exempting a bare
    # "lærer på ..." — that is the real noun title ("Lærer på ungdomstrinnet")
    # and must stay blocked.
    r"(?<!\bvi )(?<!\bdu )(?<!\bde )(?<!\bman )\blærer\b(?!\s+(?:deg|dem|opp|bort)\b)",
    r"\blærar\b", r"\blærere\b", r"\blærarar\b",
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
    # Security/threat/UX researcher exempted (2026-10-05, /fullreview deep):
    # security is the user's secondary track and these roles are not PhD
    # posts. Bare "researcher"/"senior researcher" stays blocked.
    r"\bforsker\b", r"\bforskar\b",
    r"(?<!security )(?<!threat )(?<!ux )\bresearcher\b",
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
    # English titles for the same fagbrev-gated trades. NAV carries genuinely
    # English-language ads (341 active as of 2026-09-02) and this whole list
    # was Norwegian-only apart from one ad-hoc "instrumentation technician" —
    # so a staffing agency advertising "Experienced Electricians Wanted" in
    # English sailed straight past a block that catches "elektriker".
    # Six of ten user-flagged vacancies on 2026-09-02 were exactly this.
    # Measured against every active title before adding: 25 newly-blocked,
    # every one manually checked and genuine (incl. one Norwegian-body ad
    # titled "Harrison Ford was a carpenter just like you!" whose body asks
    # for tømrere). Matched on title regardless of detected language on
    # purpose — langdetect tags short English titles like "Electrician" and
    # "Mechanic" as Norwegian, so gating on language would reopen the hole.
    # "mason" was measured too and dropped: it added no hits of its own and
    # risks matching the personal name.
    r"electrician", r"\bplumber", r"\bcarpenter", r"\bwelder\b",
    r"sheet metal work", r"steel fixer", r"\bmechanic\b", r"\bbricklayer",
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
    # advokat(?!sekretær|assistent|firma|kontor): a law-firm's secretary/
    # assistant/receptionist ("Advokatsekretær", "Kontormedarbeider
    # advokatfirma", "Resepsjonist advokatkontor") needs no bar admission
    # (2026-10-05, /fullreview deep). "advokatfullmektig" stays blocked.
    r"advokat(?!sekretær|sekretar|assistent|firma|kontor)", r"jurist", r"\brevisor", r"\bregnskapsfører",
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
# lærling(?!ansvarlig|koordinator|ordning) — the staff roles that ADMINISTER
# apprenticeships ("Lærlingansvarlig", "Lærlingkoordinator", "Rådgiver
# lærlingordning") are not apprenticeships (2026-10-05, /fullreview deep).
# Plural "lærlinger søkes" stays blocked.
APPRENTICESHIP_TITLE_PATTERNS = [r"lærling(?!ansvarlig|koordinator|ordning)", r"læreplass"]

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
    r"godkjent autorisasjon fra helsedirektoratet",
]

# Generic "authorisation required" — but NOT the conditional
# "autorisasjon kreves for søkere som er sykepleiere..." shape, which only
# requires it IF you happen to be a nurse, on a posting also open to
# assistants (live false positive 2026-07-17, Otium tilkallingsvikar).
# Split out of BODY_AUTHORISATION_PATTERNS 2026-10-05 (/fullreview deep):
# the whole module is about HEALTH authorisation (helsepersonelloven), but
# these two had no health noun at all, so "Du må ha autorisasjon for tilgang
# til driftsmiljøet" (IT access rights — the user's own target field) and
# "Arbeidet krever autorisasjon fra DSB for elvirksomhet" were blocked as if
# they were nurse ads. They now only count with a health-profession term
# within ~150 chars either side (_HEALTH_CONTEXT_RE), and never in an
# access ("tilgang") clause.
BODY_GENERIC_AUTHORISATION_PATTERNS = [
    r"krever (norsk )?autorisasjon(?! for søkere)(?! for deg som)",
    r"må ha (norsk )?autorisasjon(?! for søkere)(?! for deg som)",
]
_HEALTH_CONTEXT_RE = re.compile(
    r"helsepersonell|\bhpr\b|helsedirektorat|sykepleier|sjukepleiar|helsefagarbeid|"
    r"vernepleier|\blege\b|\blegar\b|fysioterapeut|ergoterapeut|jordmor|psykolog|"
    r"tannlege|tannpleier|farmasøyt|bioingeniør|hjelpepleier|omsorgsarbeider|"
    r"ambulansearbeider|radiograf|optiker|kiropraktor|sosionom|barnevernspedagog"
)

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
#
# Third round of this same category, 2026-09-21 (after 2026-07-18 and
# 2026-08-15 above). Live case: Skatteetaten's "du må inneha eller
# kvalifisere til sikkerhetsklarering på nivå hemmelig" slipped through.
# Both earlier rounds each added only the ONE verb phrasing that had just
# been caught missing ("sikkerhetsklareres", then "autoriseres etter
# sikkerhetsloven"), which is exactly why a third phrasing showed up —
# the regex was chasing verbs one at a time. This round keys on the NAMED
# CLEARANCE LEVEL after "sikkerhetsklarering" instead, since that's the
# actually reliable signal: a generic employer disclaimer never names a
# level, only a real per-position requirement does. Also add a narrow verb
# form ("inneha"/"kvalifisere til/for sikkerhetsklarering") for phrasings
# that don't name a level at all. A read-only audit of 10,534 active ads
# found 12 new matches, 8 of them visible/unblocked before this change, all
# genuine requirements — Nordland fylkeskommune, PST, Utenriksdepartementet,
# Statens havarikommisjon, Skatteetaten — and zero false positives; the
# "enkelte stillinger" guard below still applies to these two alternatives
# same as every other one.
SECURITY_CLEARANCE_RE = re.compile(
    r"(må kunne sikkerhetsklareres|krav(?:er)? (?:om|til) sikkerhetsklarering|"
    r"vilkår for sikkerhetsklarering|kreve(?:r)? sikkerhetsklarering|"
    r"autoriseres for (?:begrenset|konfidensielt|hemmelig|strengt hemmelig)|"
    r"klareres for (?:begrenset|konfidensielt|hemmelig|strengt hemmelig)|"
    r"autorisasjon etter sikkerhetsloven|autoriseres etter sikkerhetsloven|"
    r"sikkerhetsklarering (?:på|til|for) (?:nivå )?"
    r"(?:begrenset|konfidensielt|hemmelig|strengt hemmelig)|"
    r"(?:inneha|kvalifisere (?:til|for)) sikkerhetsklarering)"
)


# Employer-wide "SOME positions may need X" disclaimer. Widened 2026-10-05
# (/fullreview deep) from the literal "enkelte stillinger" to
# enkelte/noen/visse (+ optional "av"/"av våre") directly before "stilling":
# "Noen av stillingene i Forsvaret krever sikkerhetsklarering", "For visse
# stillinger er det krav til sikkerhetsklarering" are the same boilerplate.
# Kept adjacent (no free gap) so "Vi søker noen til stillingen" can't match.
_SOME_POSITIONS_RE = re.compile(
    r"\b(?:enkelte|noen|visse)\b(?:\s+av(?:\s+(?:våre|de|disse|alle))?)?\s+stilling"
)


def _has_definite_security_clearance_requirement(text: str) -> bool:
    """Live false-positive risk found auditing 164 real matches (2026-07-18):
    24 of them were the generic disclaimer "enkelte stillinger vil kunne
    kreve sikkerhetsklarering" ("SOME positions [in our organization] may
    require clearance") on completely unrelated postings (Tannpleier,
    Arealplanlegger, Prosjektledere at a fylkeskommune) — boilerplate about
    the employer at large, not a requirement of THIS job. Only counts a
    match as definite when no enkelte/noen/visse-stillinger quantifier
    appears shortly before it.

    2026-10-05 (/fullreview deep): each match is also judged inside its own
    clause — "Det stilles ikke krav til sikkerhetsklarering", "Du trenger
    ikke å inneha sikkerhetsklarering", "Det er en fordel å inneha
    sikkerhetsklarering", "...er ønskelig, men ikke et krav" were all
    blocked, because the regex only sees the noun phrase and was blind to
    negation/softeners around it."""
    for m in SECURITY_CLEARANCE_RE.finditer(text):
        before = text[max(0, m.start() - 80):m.start()]
        if _SOME_POSITIONS_RE.search(before):
            continue
        if _match_is_firm(text, m):
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
# "No EU passport is required" / "EU passport is not required" (2026-10-05,
# /fullreview deep) states the opposite of the tell this check looks for.
_NOT_REQUIRED_AFTER_RE = re.compile(r"\b(?:is|are)\s+(?:not|no longer)\s+(?:required|needed|necessary)\b")


def _has_eu_passport_requirement(text: str) -> bool:
    for m in EU_PASSPORT_REQUIREMENT_RE.finditer(text):
        cs, ce = _clause_bounds(text, m.start(), m.end())
        if _NOT_REQUIRED_AFTER_RE.search(text[m.end():ce]):
            continue
        if _match_is_firm(text, m):
            return True
    return False


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
    # "We are looking for someone who has:" — the long form of the heading
    # right above it. Live miss 2026-09-02: an English forklift ad listed
    # "Valid T4 forklift license" under exactly this line, so the licence
    # requirement read as unsectioned prose and the ad stayed visible.
    # The trailing colon is MANDATORY here (2026-10-05, /fullreview deep):
    # without it the alternative swallowed a whole one-line sentence
    # ("We are looking for someone who must have a valid forklift
    # certificate") as a heading, and iter_requirement_clauses `continue`s
    # on headings, so the requirement itself was never read — a regression
    # from dce4f32. A genuine heading is short ("...someone who has:").
    r"we are looking for someone who(?:\s+\w+){0,3}\s*:|"
    r"skills?\s*(?:&|and)\s*experience)\s*[:–-]*$"
)
OPTIONAL_HEADING_RE = re.compile(
    r"^(?:ønskede kvalifikasjoner|ønskelige kvalifikasjoner|ønskelig|"
    r"ønsket kompetanse|fordelaktig|det er en fordel(?:\s+om du har)?|vi ser gjerne|"
    r"personlige egenskaper|vi tilbyr|vi kan tilby|arbeidsoppgaver|"
    r"om stillingen|andre ønsker|fordeler|"
    # "Vi tilbyr deg:" / "Hos oss får du:" / "Om oss:" (2026-10-05,
    # /fullreview deep): benefit/employer-intro sections were not recognised,
    # so "Opplæring og truckførerbevis (T4)" listed as a perk under them
    # stayed in the preceding requirement section and blocked the ad.
    r"vi tilbyr deg|hos oss får du|om oss)\s*[:–-]*$"
    r"|^(?:we offer|responsibilities|nice to have|preferred qualifications|benefits|"
    r"personal qualities|what we offer|desired qualifications)\s*[:–-]*$"
)
# A dot ends a sentence unless it sits BETWEEN digits ("1.5", "31.12.2026").
# The old `(?<![0-9])\.(?![0-9])` also refused to split after a digit at a
# sentence end, so "Krav: truckførerbevis T4. Vi tilbyr gode fordeler." stayed
# ONE clause and the second sentence's softener disarmed the requirement
# (2026-10-05, /fullreview deep). The second alternative splits a
# digit-then-dot only when whitespace/end follows ("T4. Vi", "31.12. Vi").
# Side effect, accepted: a numbered-list marker "1. Foo" now splits into "1"
# and "Foo" — harmless, section state is per line, not per clause.
_CLAUSE_SPLIT_RE = re.compile(r"(?<![0-9])\.(?![0-9])|(?<=[0-9])\.(?=\s|$)|[;!?]")
# Same boundaries plus line breaks — for locating the clause around a match.
_CLAUSE_OR_LINE_RE = re.compile(r"\n|" + _CLAUSE_SPLIT_RE.pattern)


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
    r"\bis required\b|\bare required\b|\bmust have\b|\bmust hold\b|\brequired\b|"
    # English twin of "gyldig" above — "Valid T4 forklift license" states a
    # hard requirement on its own, with or without a recognised heading over
    # it (live miss 2026-09-02). Safe as a bare word here because a verb only
    # matters in a clause that already contains a specific mention pattern.
    r"\bvalid\b"
)
# A softener anywhere in the clause wins even under a requirements heading
# ("Kvalifikasjoner: ... truckførerbevis er en fordel, men ikke et krav" —
# measured live, ~55 of 118 truckfør-mentioning ads use exactly this shape).
OPTIONAL_MARKER_RE = re.compile(
    # "fordel" used to be a bare substring, so "Vi tilbyr gode fordeler"
    # (benefits) and "fordelt på" (distributed) softened any requirement in
    # the same clause (2026-10-05, /fullreview deep). Now only the singular
    # advantage: "fordel", "fordelen", "fordelaktig(e)", and compounds
    # ("konkurransefordel") — right-anchored, no left \b on purpose.
    r"gjerne|ønskelig|ønskjeleg|fordel(?:en|aktig\w*)?\b|pluss\b|positivt|"
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

# A qualifier hanging off the END of a requirement softens the detail it
# names, not the requirement itself: "har gyldig truckførerbevis, gjerne
# T1--T4" requires the licence and merely prefers those classes, and
# "førerkort klasse B er et absolutt krav, gjerne BE" says so outright.
# Same reasoning that already scopes OPTIONAL_MARKER_RE outside parentheses
# below ("Truckførerbevis T8 (T8.4 er en fordel)") — this generalises it to
# the comma form. Deliberately narrow: only strips when the trailing segment
# *opens* with a bare preference adverb, so a clause that was soft from the
# start ("det er ønskelig at du har erfaring, gjerne fra motebransjen")
# keeps its leading softener and stays soft.
_TRAILING_QUALIFIER_RE = re.compile(r",\s*(?:gjerne|helst|fortrinnsvis|ideelt sett|primært)\b.*$")


def has_optional_marker(clause: str) -> bool:
    """Is this clause softened as a whole? Ignores softeners that only
    qualify a trailing or parenthesised detail. Shared by every soft/hard
    decision (hard_blocks' truckfør/forklift checks and scoring.py's
    car/formal-qualification/programming checks) so the scoping rule can't
    drift between them — it used to live inline in one of the five."""
    scoped = _TRAILING_QUALIFIER_RE.sub("", _PARENS_RE.sub(" ", clause))
    return bool(OPTIONAL_MARKER_RE.search(scoped))


def _clause_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    """(start, end) of the clause (sentence within a line) containing the
    span [start, end) — same boundaries iter_requirement_clauses splits on."""
    cuts = list(_CLAUSE_OR_LINE_RE.finditer(text))
    ends = [c.end() for c in cuts]
    i = bisect_right(ends, start)
    cs = ends[i - 1] if i else 0
    ce = len(text)
    for c in cuts:
        if c.start() >= end:
            ce = c.start()
            break
    return cs, ce


# Negation in the text BEFORE a mention, within its own clause segment
# ("Det stilles ikke krav til X", "Ingen krav om X", "Du trenger ikke å
# inneha X", "No EU passport"). Only the segment after the last comma / "men"
# / "but" counts, so "Stillingen er ikke deltid, men krever X" stays firm.
_NEGATION_BEFORE_RE = re.compile(r"\b(?:ikke|ikkje|ingen|intet|uten)\b")
# English negators are only trusted in the last ~3 words before the match
# ("No EU passport", "does not require an EU passport"). Bare "no" is Nynorsk
# for "now" ("Vi søkjer no ein IT-konsulent som må kunne sikkerhetsklareres" —
# very common in Vestland ads), and an "English" negator earlier in a long
# clause says nothing about the match (follow-up 2026-10-05, /fullreview deep).
_NEGATION_EN_NEAR_RE = re.compile(r"\b(?:no|not|without)\b")
_NEGATION_EN_WINDOW_WORDS = 3
_NEGATION_SEGMENT_SPLIT_RE = re.compile(r",|\bmen\b|\bbut\b")


def _match_is_firm(text: str, m: "re.Match[str]") -> bool:
    """Is the body-level pattern match `m` stated as a firm requirement?
    Judges the match inside its own clause: false when the clause carries a
    softener (has_optional_marker: "en fordel", "ønskelig, men ikke et krav")
    or the match is negated. Added 2026-10-05 (/fullreview deep) — the
    body-level clearance/authorisation/EU-passport checks used to be bare
    regex hits, blind to "ikke krav om ...", "en fordel med ...", "No EU
    passport is required"."""
    cs, ce = _clause_bounds(text, m.start(), m.end())
    if has_optional_marker(text[cs:ce]):
        return False
    before = _NEGATION_SEGMENT_SPLIT_RE.split(text[cs:m.start()])[-1]
    if _NEGATION_BEFORE_RE.search(before):
        return False
    near = " ".join(before.split()[-_NEGATION_EN_WINDOW_WORDS:])
    return not _NEGATION_EN_NEAR_RE.search(near)


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
        if has_optional_marker(clause):
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

_HSPACE_RE = re.compile(r"[ \t\xa0]+")


def _normalize_text(text: str | None) -> str:
    """NFC + collapse runs of horizontal whitespace (incl. NBSP) to one
    space, keeping newlines (the clause machinery needs them). Added
    2026-10-05 (/fullreview deep): a decomposed "å"/"ø" (NFD, e.g. from a
    pasted/Mac-origin title) or a double/non-breaking space ("Lager\xa0
    medarbeider", "Vi\xa0trenger") silently defeated patterns that
    contain a literal space or a precomposed letter. Idempotent, so the
    body is normalised here too even though db.strip_html also does it."""
    return _HSPACE_RE.sub(" ", unicodedata.normalize("NFC", text or ""))


def _has_body_authorisation_requirement(body_l: str) -> bool:
    """Body-level authorisation requirement. Every hit is judged in its own
    clause (negation / softener / "enkelte-noen-visse stillinger"
    employer-wide disclaimer) via _match_is_firm — "Ingen krav om
    autorisasjon som sykepleier", "Det er en fordel med autorisasjon som
    helsefagarbeider" are not requirements (2026-10-05, /fullreview deep).
    The generic "krever/må ha autorisasjon" alternatives additionally need a
    health-profession term nearby and may not be an access ("tilgang")
    clause — see BODY_GENERIC_AUTHORISATION_PATTERNS."""
    for pattern in BODY_AUTHORISATION_PATTERNS + BODY_GENERIC_AUTHORISATION_PATTERNS:
        generic = pattern in BODY_GENERIC_AUTHORISATION_PATTERNS
        for m in re.finditer(pattern, body_l):
            if _SOME_POSITIONS_RE.search(body_l[max(0, m.start() - 80):m.start()]):
                continue
            if not _match_is_firm(body_l, m):
                continue
            if generic:
                cs, ce = _clause_bounds(body_l, m.start(), m.end())
                if "tilgang" in body_l[cs:ce]:
                    continue
                if not _HEALTH_CONTEXT_RE.search(body_l[max(0, m.start() - 150):m.end() + 150]):
                    continue
            return True
    return False


def check_exclusion(
    title: str | None,
    description_text: str | None,
    county: str | None = None,
    extent_percent: int | None = None,
) -> tuple[bool, str | None]:
    """Returns (is_excluded, human-readable reason in Ukrainian)."""
    title_l = _normalize_text(title).lower()

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

    body_l = _normalize_text(description_text).lower()
    if _has_body_authorisation_requirement(body_l):
        return True, "В описі прямо вимагається норвезька авторизація"

    if _has_definite_security_clearance_requirement(body_l):
        return True, "Потрібен допуск до державної таємниці (sikkerhetsklarering) — недосяжно без громадянства"

    if _has_eu_passport_requirement(body_l):
        return True, "Вакансія фізично за кордоном (вимагає EU passport), не в Норвегії"

    if _has_unmet_truckforerbevis_requirement(title_l, body_l):
        return True, "Вимагає truckførerbevis без навчання на місці — поки не отримуємо"

    if _has_unmet_forklift_certificate_requirement(title_l, body_l):
        return True, "Вимагає forklift certificate без навчання на місці — поки не отримуємо"

    return False, None
