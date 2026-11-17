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

import functools
import re
from bisect import bisect_right

from textnorm import normalize_text

# re.search(str, ...) re-resolves the pattern through re's module cache on every
# call; check_exclusion runs ~130 title patterns per row. Same compiled objects,
# one dict hit.
_compile = functools.lru_cache(maxsize=None)(re.compile)

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
    r"tannlege(?!assistent)", r"\btannpleier", r"\btannhelsesekret",
    r"fysioterapeut", r"ergoterapeut",
    # 2026-10-22 compound/Nynorsk/plural gaps, each read on the live titles
    # (all genuine authorisation-gated roles, 0 false positives except the one
    # guarded): "Tannlegar og tannpleiarar" (Nynorsk plurals), "Assistenttannlege"
    # (a licensed dentist, unlike tannlegeassistent), "Spesialergoterapeut" x3 and
    # kommunefysioterapeut (the \b on -terapeut hid compounds), "Barnelege",
    # "Sjefslege" x2, Bokmål plural "leger" ("WRK Medic søker leger!") - but never
    # "Leger Uten Grenser" (employer). Bare "lege\b" was measured and REJECTED:
    # it also hits "daglege" ("team som daglege ved en av Norges største
    # legevakter"), so only these explicit words.
    r"tannlegar", r"tannpleiar", r"barnelege", r"sjefslege", r"\bleger\b(?! uten grenser)",
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
    # Corpus audit 2026-11-17: authorised-profession titles that were visible
    # (read on the live titles): Veterinær x6, Dyrepleier x6 (protected title
    # under dyrehelsepersonelloven), Audiograf x8, Psykiater x3, Apoteker/
    # Sykehusapoteker x4, Radiolog(ist), Gynekolog, Stråleterapeut, "Fagradiograf"
    # (the \b on radiograf hid the compound), LIS 1/2/3 (doctors in
    # specialisation), Overlegevikariat, Legevikar(iat), "Medisinstudent/Cand.med.
    # med lisens" (a student LICENCE he lacks) and English "Anaesthetic Nurse" /
    # "Travel Nurse" / "Consultant Interventional Cardiologist".
    # Helsesekretær is NOT here: its ads routinely accept "annen relevant
    # utdanning", so it is judged on the body (authorised-profession bullets).
    r"veterinær(?!deg)",  # (?!deg): the employer name "VeteriNærDeg as" in a receptionist ad
    r"dyrepleier", r"audiograf", r"psykiater", r"\bapoteker\b", r"sykehusapoteker",
    r"radiolog(?!i(?!st))", r"gynekolog(?!i(?!st))", r"kardiolog(?!i(?!st))", r"stråleterapeut", r"fagradiograf", r"\blis ?[123]\b",
    r"overlege(?:vikar|stilling)", r"legevikar", r"medisinstudent", r"legestudent", r"cand\.? ?med",
    r"\bnurse\b", r"\bphysician", r"\bsurgeon",
    # Same audit, second pass over the titles still visible: "Avdelingsoverlege" (the \boverlege\b
    # above misses compounds), "Legespesialist?", "sakkyndig lungelege", "spesialist i
    # indremedisin", "Forskningsoptiker".
    r"\w*overlege\b", r"legespesialist", r"lungelege",
    r"spesialist i (?:indremedisin|\w*kirurgi|\w*psykiatri|radiologi|fødselshjelp|\w*medisin)", r"forskningsoptiker",
]

# Teaching / pedagogical posts requiring formal Norwegian pedagogical
# qualification (godkjent lærerutdanning).
TEACHING_TITLE_PATTERNS = [
    # "lærer" is also the VERB ("Vi lærer deg opp som lagermedarbeider" —
    # 2026-10-05, /fullreview deep): skip it when a pronoun subject precedes
    # and deg/dem/opp/bort follows. Deliberately NOT exempting a bare
    # "lærer på ..." — that is the real noun title ("Lærer på ungdomstrinnet")
    # and must stay blocked.
    # 2026-10-22: no leading \b so compounds are caught - kontaktlærer, faglærer,
    # norsklærer, grunnskolelærer, musikklærer, yrkesfaglærer, Nynorsk
    # kulturskulelærar/yrkesfaglærar (37 live titles, every one a school/music/
    # dance teacher; the pattern already blocked "Lærer i IKT", so an IT-teacher
    # post is not a new case). trafikklærer/kjørelærer are a different profession
    # (driving instructor) and handled as a LICENCE block below instead.
    r"(?<!trafikk)(?<!kjøre)(?<!\bvi )(?<!\bdu )(?<!\bde )(?<!\bman )lærer\b(?!\s+(?:deg|dem|opp|bort)\b)",
    r"(?<!trafikk)(?<!kjøre)lærar\b", r"(?<!trafikk)(?<!kjøre)lærere\b", r"(?<!trafikk)(?<!kjøre)lærarar\b",
    # "Lærerstillinger Manglerud skole", "Fast lærerstilling", Nynorsk
    # "Lærarstilling", "Ingeniør innen Automasjon søkes til lærerstilling" (7 live).
    r"lærerstilling", r"lærarstilling",
    r"\blærervikar", r"\blærarvikar",
    r"\badjunkt", r"\blektor",
    r"\bbarnehagelærer", r"\bbarnehagelærar",
    r"\bpedagogisk leder", r"\bpedagogisk leiar",
    r"\bpedagog\b", r"\bspesialpedagog",
    # Corpus audit 2026-11-17: the plural "Pedagogiske ledere" (x9) and the
    # pedagog COMPOUNDS slipped past \bpedagog\b: Støttepedagog, Ressurspedagog(er),
    # Fagpedagog, Montessoripedagog, Språkpedagog, Teiknspråkspedagog,
    # Ungdomskolepedagog, "Pedagogvikariat" - all posts that need the formal
    # pedagogical education. "Pedagogisk medarbeider" (the assistant role, no
    # requirement) and Menighetspedagog (church staff) stay visible.
    r"\bpedagogiske? lede(?:r|re|iar)(?!.*(?:assistent|fagarbeid))", r"(?:støtte|ressurs|fag|montessori|språk|teiknspråks|ungdomsskole|ungdomskole)pedagog",
    r"\bpedagogvikar", r"undervisningsstilling", r"undervisningsvikariat",
    # School principals ("Rektor", "Assisterende rektor", 9 live) need godkjent lærerutdanning (the
    # bodies say "krav om godkjend lærarutdanning"); "Prorektor" has no \b before "rektor".
    r"\brektor\b",
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
    # 2026-10-22, three lecturer posts that slipped through (academic, not K-12:
    # they belong here and not in TEACHING_TITLE_PATTERNS' bare \blektor, which
    # would also mislabel them): Bokmål "Høyskolelektor innen administrative fag"
    # (HVL; only the Nynorsk "høgskole" forms were listed), "Museumslektor" x2
    # (UiO), "Allaskuvlalektor" x2 (Sámi allaskuvla). A bare "lektor" substring
    # was rejected: "kollektor" is a solar/pipe collector.
    r"høyskolelektor", r"høyskolelærer", r"museumslektor", r"allaskuvlalektor",
    # Nynorsk "Høgskulelektor i norsk" (corpus audit 2026-11-17).
    r"høgskulelektor", r"høgskulelærar",
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
    # 2026-10-22: "Tilkallingsfrisør" (a hairdresser) slipped through the bare \b;
    # a lookbehind keeps the pet groomers exempt instead (hundefrisør x4 live,
    # "hunde- og kattefrisør").
    r"(?<!hunde)(?<!katte)(?<!dyre)frisør",
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
    r"\bstyrmann", r"\boverstyrmann", r"maskinist", r"\bskipsfører",
    # matros dropped its leading \b same pass — "lettmatros" (ordinary/
    # junior seaman) was missed; checked, 0 false positives.
    r"matros", r"\bkaptein", r"\bmaskinsjef",
    # 2026-10-22, live titles (all passenger/ferry/cargo vessel crew, scored 25-35
    # in the warehouse profile via NAV "Transport og lager"): "Skipper (D5)" x2,
    # "Båtførar", "Maskinpasser" x3 (incl. "Motormann/Maskinpasser"), "Sjøvakt" x3,
    # "Pumpemann til Ro-Ro", "Skipsførar til hurtigbåtane" (the Nynorsk twin of
    # skipsfører) and, with the leading \b dropped, "Førstemaskinist"/"Andremaskinist".
    r"skipper", r"båtfør", r"maskinpasser", r"sjøvakt", r"pumpemann", r"skipsførar",
    # Driving instructors need the very licence the user does not have: 5 live
    # "Trafikklærer(e/ar)" ads (the teacher patterns above exempt them).
    r"trafikklærer", r"trafikklærar",
    # Corpus audit 2026-11-17: airline pilot ("Pilot" at Widerøe = ATPL + medical).
    # "UAS Pilot"/"ROV Pilot" (drone / subsea operators) are deliberately NOT here.
    r"^pilot$",
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
    # Corpus audit 2026-11-17: "Eiendomsmegler"/"Eiendomsmegler fullmektig" need the statutory
    # meglerbevilling; compounds (eiendomsmeglerkjede, eiendomsmegler.no) are not the job.
    r"eiendomsmegler(?![a-zæøå.])",
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
    # Nynorsk, 2026-10-22: "juridisk rådgjevar" (HVL, 2 vikariat posts).
    r"juridisk rådgjevar", r"juridiske rådgjevarar",
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
    # Corpus audit 2026-11-17: ads titled by the rank code ("Etterforsker PB 3/2/1",
    # "Etterforskere PB 3/2/1" x3 - the body says "lønnes som politibetjent"), a bare
    # "Politi", "Grunnstilling politi" and "Politifaglig etterforskningsleder" x4 - all
    # require the police-academy education. Civilian "Etterforsker"/"Politisk ..."
    # titles stay visible (see the comment above).
    r"\bpb ?[0-3]\b", r"^politi$", r"grunnstilling politi", r"politifaglig",
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

# A role advertised FOR speakers of a language the user does not speak
# (he has Russian, Ukrainian, English, learning Norwegian). Flagged 2026-11-03:
# "Svensktalende IT-support tekniker" (Sopra Steria) scored 36. Live titles with
# the shape: "Administrasjonsmedarbeider (polsktalende)", "...German-speaking Key
# Account Manager", "polsk-og norsktalende BPA-assistent". Never russisk/ukrainsk/
# engelsk/norsk - those he speaks ("norsk og ukrainsk/russisk talende" stays).
# The optional "og/eller <word>" bridge catches "svensk og norsktalende" (he
# cannot do the Swedish half). Title only: 9 body-level hits live, none >= 30.
_FOREIGN_LANG = r"(?:svensk|dansk|finsk|tysk|fransk|nederlandsk|spansk|italiensk|polsk|islandsk)"
_FOREIGN_LANG_EN = r"(?:swedish|danish|finnish|german|french|dutch|spanish|italian|polish|icelandic)"
FOREIGN_LANGUAGE_TITLE_PATTERNS = [
    _FOREIGN_LANG + r"[- ]?(?:(?:og|eller|/)[- ]?[a-zæøå]+)?[- ]?talende",
    r"\b" + _FOREIGN_LANG_EN + r"[- ]speaking\b",
]

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
    ("fremmedspraak", FOREIGN_LANGUAGE_TITLE_PATTERNS, "Потрібна мова, якою я не володію (шведська/данська/німецька/польська тощо)"),
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
# Corpus audit 2026-11-17: ~100 visible ads listed an authorised profession as a
# plain qualification bullet and only the one-profession-per-pattern shape above
# knew it: "Norsk autorisasjon som helsesekretær" x6, "... som audiograf/jordmor/
# radiograf/veterinær/provisorfarmasøyt", "Autorisert sykepleier/vernepleier",
# "sykepleier med norsk autorisasjon", "Autorisert helsefagarbeider eller
# sykepleier" (a list of authorised professions), "norsk spesialistgodkjenning i ...".
# So the profession is now a shared LIST (with compounds/plurals, never "-student")
# and the three shapes - "autorisert X", "autorisasjon som X", "X med autorisasjon" -
# reuse one tail of guards. The list is matched possessively on purpose: in
# "autorisert sykepleier eller IKT-utdanning" the list stops at "sykepleier", the
# "eller" guard fires, and backtracking may not shorten the match around it.
_AUTH_ONE = (
    r"(?:[a-zæøå]*(?:sykepleier|sjukepleier|sjukepleiar|helsefagarbeid(?:er|ar)|vernepleier|vernepleiar|hjelpepleier|hjelpepleiar|"
    r"omsorgsarbeid(?:er|ar)|ambulansearbeid(?:er|ar)|paramedisin(?:er)?|paramedic|tannlege|tannpleier|tannpleiar|"
    r"tannhelsesekretær|fysioterapeut|ergoterapeut|jordmor|jordmødre|psykolog|psykiater|farmasøyt|apotektekniker|"
    r"bioingeniør|radiograf|audiograf|optiker|kiropraktor|helsesekretær|veterinær|dyrepleier|dyrepleiar|oralkirurg|"
    r"helsepersonell)|\b(?:over|spesial|fast)?(?:lege|legar)|\bspesialist)"
    r"(?:e|er|ere|ar|arar|en|utdanning|utdannelse)?(?![a-zæøå])"
)
_AUTH_LIST = _AUTH_ONE + r"(?:-?\s*(?:/|,|og/eller|og|eller)\s*" + _AUTH_ONE + r")*+"
_AUTH_NOT_PREFERRED = r"(?<!fortrinnsvis )(?<!primært )(?<!gjerne )(?<!helst )(?<!for )(?<!for de som er )(?<!for de som har )"
# What follows the profession list decides whether it is ONE firm requirement or one of several
# alternatives. "eller X" / "/ X" is an alternative the user might meet ("eller IKT-utdanning"),
# unless X is itself a not-yet-qualified health route (students, "i gang med", "venter på godkjenning").
# A comma continues the requirement ("..., gjerne med erfaring", "..., ønsker korte vikariater")
# unless it opens an alternative: "eller/evt/annen/tilsvarende", a *sekretær* ("autorisert
# helsesekretær, medisinsk sekretær, sekretær"), a fagbrev, "relevant utdanning".
_AUTH_ALT_IS_HEALTH_ROUTE = r"(?![^.\n]{0,50}?(?:student|utdanningsløp|studieløp|under utdanning|i gang med|løp mot|venter på godkjenning|nyutdannet))"
_AUTH_COMMA_ALTERNATIVE = (
    r"(?!\s*,\s*(?:eller|og/eller|evt|eventuelt|alternativt|annen|anna|andre|tilsvarende|(?:[a-zæøå-]+\s+)?[a-zæøå-]*sekret[æa]r|fagbrev|"
    r"relevant\s+(?:utdanning|utdannelse|bakgrunn|kompetanse))\b)(?!\s*,?\s*og/eller)"
)
_AUTH_TAIL = (
    r"(?!\s*(?:eller|/)" + _AUTH_ALT_IS_HEALTH_ROUTE + r")(?! for søkere)(?! for deg som)" + _AUTH_COMMA_ALTERNATIVE +
    r"(?!(?:(?!\bog\b)[^.\n]){0,60}?\beller (?:annen|andre|tilsvarende|lignende))(?![^\n]{0,40}ufaglært)"
)
BODY_AUTHORISATION_PATTERNS = [
    r"godkjent autorisasjon fra helsedirektoratet",
    # "Autorisert Radiograf" as a bare qualification bullet (Vestre Viken
    # IKT-rådgiver, 2026-11-13). Needs a profession from _AUTH_LIST, so the employer
    # self-description "autorisert lærebedrift" never matches; "eller"/"/" followed
    # by a non-profession means an alternative ("autorisert sykepleier eller IKT-utdanning").
    # "Offentlig godkjent sykepleier" (Ahus Rådgiver IKT, 2026-11-17) is the legal synonym
    # of "autorisert" under helsepersonelloven: same pattern, same guards.
    _AUTH_NOT_PREFERRED + r"\b(?:autorisert|autoriserte|offentlig godkjent|offentlig godkjenning|offentleg godkjend|"
    r"godkjent/autorisert) (?:utdanning |utdannelse )?(?:som )?" + _AUTH_LIST + _AUTH_TAIL,
    # "autorisasjon som sykepleier" as a listed qualification (a bare bullet or
    # "du har norsk autorisasjon som sykepleier"), also "norsk autorisasjon som
    # helsesekretær eller sykepleier".
    _AUTH_NOT_PREFERRED + r"\b(?:(?:norsk|gyldig|godkjent) )*autorisa(?:sj|j)on (?:(?:som|innen) )?" + _AUTH_LIST + _AUTH_TAIL,
    # "sykepleier med (norsk) autorisasjon", "sykepleierutdanning med autorisasjon".
    _AUTH_NOT_PREFERRED + r"\b" + _AUTH_LIST + r"(?:,? (?:med|m/)| må ha| har) (?:gyldig |godkjent )?(?:norsk )?autorisa(?:sj|j)on(?! for søkere)(?! for deg som)"
    r"(?!(?:(?!\bog\b)[^.\n]){0,60}?\beller (?:annen|andre|tilsvarende|lignende))(?![^\n]{0,40}ufaglært)",
    # A Norwegian specialist approval is the doctor/dentist-level version of the same thing.
    r"\bnorsk spesialistgodkjenning\b",
]
# Softeners around the match that _match_is_firm's sub-segment view cannot see: in the same
# clause, the rest of the line and the NEXT line ("Autorisert helsesekretær. Andre
# yrkesgrupper med erfaring fra legekontor er velkommen til å søke" / "Autorisert
# helsesekretær\nAnnen relevant utdanning kan vurderes"). "eventuelt" opens an alternative.
_AUTH_SOFT_AROUND_RE = re.compile(
    r"foretrekk|føretrekk|føretrekt|foretrukket|eventuelt|\bev\.|velkommen til å søke|er også velkommen|"
    r"kan også|kan òg|kan vurderes|vurderes også|kan komme i betrakning|kan være aktuell|kan vere aktuell|"
    r"oppfordres|oppfordrast|ufaglært|ikke et krav|ikkje (?:eit )?krav|kan fravikes"
)
# "Har du kontorfaglig bakgrunn eller er du autorisert helsepersonell?" - the match is itself an alternative.
_AUTH_ALTERNATIVE_BEFORE_RE = re.compile(r"\beller\s+(?:er|har|som)\s+(?:du\s+)?(?:\w+\s+){0,2}$")

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
# requires Norwegian citizenship in practice (sikkerhetsloven), so it is
# unreachable for a non-citizen whatever their residence status. Reported
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
#
# Fifth round, 2026-10-27: Kongsberg Defence's "må du kunne sikkerhetsklareres
# og autoriseres jfr. Sikkerhetsloven" slipped through — a subject between
# "må" and "kunne" (up to 3 words: du / vedkommende / den som ansettes), and
# "jfr."/"jf."/"i henhold til"/"iht." instead of "etter" before the law name.
#
# Sixth round, 2026-11-13: Eccera's "Stillingen krever at den aktuelle
# kandidaten kan sikkerhetsklareres i Norge og NATO" — no "må", the verb is
# "krever/forutsetter at <subject> kan/kunne". Bare "kan sikkerhetsklareres"
# stays unmatched (hedges: "kan kreve at du kan sikkerhetsklareres").
SECURITY_CLEARANCE_RE = re.compile(
    r"(må (?:\w+ ){0,3}kunne sikkerhetsklareres|"
    r"(?:krever|forutsetter|forutsetning|krav om) at (?:\w+ ){0,6}?(?:kan|kunne) sikkerhetsklareres|"
    r"sikkerhetsklareres i norge og nato|krav(?:er)? (?:om|til) sikkerhetsklarering|"
    r"vilkår for sikkerhetsklarering|kreve(?:r)? sikkerhetsklarering|"
    r"autoriseres for (?:begrenset|konfidensielt|hemmelig|strengt hemmelig)|"
    r"klareres for (?:begrenset|konfidensielt|hemmelig|strengt hemmelig)|"
    r"autorisasjon etter sikkerhetsloven|"
    r"autoriseres (?:etter|jfr?\.?|i henhold til|iht\.?) sikkerhetsloven|"
    r"sikkerhetsklarering (?:på|til|for) (?:nivå )?"
    r"(?:begrenset|konfidensielt|hemmelig|strengt hemmelig)|"
    r"(?:inneha|kvalifisere (?:til|for)) sikkerhetsklarering|"
    r"(?:tilfredsstille|oppfylle)r?\s+(?:\w+\s+){0,2}kravene?\s+(?:til|om)\s+sikkerhetsklarering|"
    r"opprettholde[rt]?\s+(?:\w+\s+){0,1}(?:sikkerhets)?klarering)"
)
# Seventh round, 2026-11-13: NSM's "Videre må du tilfredsstille kravene til
# sikkerhetsklarering og autorisasjon på høyeste nivå. Det er en forutsetning
# at du opprettholder din klarering" — "kravene" (definite plural) and a
# verb other than "inneha"/"krever" were not covered.

# Hard Norwegian-citizenship-only requirement. The user is not a Norwegian
# citizen, so such ads are unreachable whatever they say about clearance.
# Judged per clause via _match_is_firm like the clearance rule, so "ikke krav
# om norsk statsborgerskap" stays unblocked; "uavhengig av statsborgerskap"
# and citizenship of the people the job serves do not match.
# Corpus audit 2026-11-17 widened it (every phrasing a real ad): Etterretningstjenesten's
# "kan man ikke ha annet enn norsk statsborgerskap" x20 (only "inneha ... statsborgerskap
# enn norsk" was covered) and its Nynorsk twin "kan du ikkje ha anna enn norsk
# statsborgarskap"; Nynorsk "du må vere norsk statsborgar" (politiet, x4) and a typo "norst";
# bare bullets ("Norsk statsborgerskap", "Mulighet til å sikkerhetsklareres og norsk
# statsborgerskap") - see _CITIZENSHIP_BULLET_RE; English "must hold Norwegian citizenship".
NORWEGIAN_CITIZENSHIP_ONLY_RE = re.compile(
    r"(?:(?:kan|må)\s+(?:\w+\s+){0,2}?(?:ikke|ikkje)\s+(?:inneha|ha)\s+(?:noe\s+|noko\s+)?(?:annet|anna)\s+"
    r"(?:(?:statsborgerskap|statsborgarskap)\s+)?enn\s+norsk(?:\s+(?:statsborgerskap|statsborgarskap))?|"
    r"må\s+(?:\w+\s+){0,3}(?:(?:være|vere)\s+nors[kt]e?\s+(?:statsborger|statsborgar)|"
    r"ha\s+norsk\s+(?:statsborgerskap|statsborgarskap))|"
    r"kun\s+nors[kt]e?\s+(?:statsborger|statsborgar)|"
    r"krav\s+om\s+norsk\s+(?:statsborgerskap|statsborgarskap)|"
    r"norsk\s+(?:statsborgerskap|statsborgarskap)\s+er\s+(?:et|en|eit)\s+(?:krav|forutsetning)|"
    r"(?:must|have\s+to|need\s+to)\s+(?:be\s+a|hold|have)\s+norwegian\s+(?:citizen|national|citizenship)|"
    r"(?:only\s+norwegian\s+(?:citizens|nationals)|norwegian\s+(?:citizens|nationals)\s+only)|"
    r"norwegian\s+citizenship\s+is\s+(?:required|mandatory|a\s+requirement))"
)
# A whole-line bullet naming the citizenship: "Norsk statsborgerskap", "Mulighet til å
# sikkerhetsklareres og norsk statsborgerskap". Lines with a softener, a negation or an
# alternative are left alone (the \w*-word list below).
_CITIZENSHIP_BULLET_RE = re.compile(
    r"^\W*(?:(?:[a-zæøå]+\s+){0,6}og\s+)?(?:(?:må\s+)?være\s+)?norsk(?:e)?\s+statsborger\w*\W*$", re.M
)
_CITIZENSHIP_BULLET_SOFT_RE = re.compile(r"\b(?:ikke|ikkje|uavhengig|gjerne|fordel|ønsk\w*|eller|kan|bør|helst)\b")
# EEA-only: "Du må være EØS-borger", a bare "EU/EØS borger" bullet. The user holds a Norwegian
# residence/work permit, so ONLY an ad that names no permit alternative counts.
_EEA_CITIZENSHIP_RE = re.compile(
    r"må\s+(?:\w+\s+){0,3}være\s+(?:eu/eøs|eøs|eu|nordisk)[- ]?(?:borger|statsborger)\w*|"
    r"^\W*(?:eu/eøs|eøs)[- ]?borger\w*\W*$", re.M
)
_EEA_PERMIT_ALTERNATIVE_RE = re.compile(r"\beller\b|\bor\b|arbeidstillatelse|oppholdstillatelse|work permit|permit|tillatelse")
NORWEGIAN_CITIZENSHIP_ONLY_REASON = "Вимагається лише норвезьке громадянство — недосяжно"


def _has_norwegian_citizenship_only_requirement(text: str) -> bool:
    if any(_match_is_firm(text, m) for m in NORWEGIAN_CITIZENSHIP_ONLY_RE.finditer(text)):
        return True
    for m in _CITIZENSHIP_BULLET_RE.finditer(text):
        if not _CITIZENSHIP_BULLET_SOFT_RE.search(m.group()) and _section_before(text, m.start()) != "opt":
            return True
    for m in _EEA_CITIZENSHIP_RE.finditer(text):
        cs, ce = _clause_bounds(text, m.start(), m.end())
        if not _EEA_PERMIT_ALTERNATIVE_RE.search(text[cs:ce]) and _match_is_firm(text, m):
            return True
    return False


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


# Fourth round of this category, 2026-10-25, and the first ENGLISH one: the
# Norwegian regex above never sees English-language ads. Live case: Six
# Robotics (Oslo defence drone-swarm company working with FFI), "IT &
# Infrastructure Engineer" scored 54 and was visible with "All employees at
# Six Robotics must be eligible for security clearance" (its other ads say
# "are required to undergo a security clearance due to our close
# collaboration with the norwegian armed forces"). Kept as a SEPARATE regex —
# English needs its own guard and must not loosen the Norwegian one. The word
# "security" before "clearance" is mandatory: CERN's "must obtain medical
# clearance" and Monil's "market clearances" must not match. The sentence-
# scoped guard below exists for employer-wide disclaimers — Vard Group "this
# employment may require ... security clearance", Robotto "certain positions
# may be subject to security-clearance". Read-only audit of 11,097
# active ads found 19 new matches, 14 of them visible/unblocked before this
# change (Six Robotics x11, University of Agder postdoc, IFE
# senior scientist, Kongsberg Maritime hydrodynamicist; NTNU PhDs were
# already excluded by other rules), all genuine requirements, zero false
# positives; Vard, Robotto, CERN and Monil stay unmatched.
SECURITY_CLEARANCE_EN_RE = re.compile(
    r"(?:(?:obtain|hold|get|receive|undergo|pass|qualify for|meet)\b[^.]{0,40}\bsecurity clearance|"
    r"eligible (?:for|to obtain)[^.]{0,20}security clearance|"
    r"(?:requires?|requiring)\b[^.]{0,25}security clearance|"
    r"security clearance[^.]{0,40}\b(?:is|are|will be)\b[^.]{0,10}"
    r"(?:required|mandatory|a requirement|a prerequisite)|"
    r"(?:subject to|conditional on|contingent (?:up)?on)[^.]{0,50}security clearance)"
)
_EN_CLEARANCE_SOFTENER_RE = re.compile(
    r"\bmay |\bmight |\bcould |certain positions|certain roles|some positions|"
    r"some roles|depending on"
)
_EN_CLEARANCE_NEGATED_RE = re.compile(r"not required|not a requirement")


def _has_definite_security_clearance_requirement_en(text: str) -> bool:
    for m in SECURITY_CLEARANCE_EN_RE.finditer(text):
        # Sentence start = the clause boundary (". ", a line break, ;!?). It used to be only
        # the last ". ", so a hedge on the PREVIOUS line leaked in: Vard's "This employment
        # may require a pre-employment check ... \n This employment requires security
        # clearance by NSM" kept the "may" of the line above (corpus audit 2026-11-17).
        sentence_start = _clause_bounds(text, m.start(), m.end())[0]
        if _EN_CLEARANCE_SOFTENER_RE.search(text[sentence_start:m.start()]):
            continue
        if _EN_CLEARANCE_NEGATED_RE.search(text[m.end():m.end() + 30]):
            continue
        return True
    return False


# --- Corpus audit 2026-11-17: ONE stem-based clearance judge --------------------
# Seven rounds of this category each added the single verb phrasing that had just
# slipped through. A read of every clearance sentence in the live corpus (~170
# distinct contexts) showed the regexes above still missed ~25 real requirements:
# the participle "kvalifisert for å bli sikkerhetsklarert, jfr. sikkerhetsloven"
# (Kongsberg x34), "må tilfredsstille kravene til HØYESTE sikkerhetsklarering"
# (Etterretningstjenesten x20, one word between kravene and til), "må kunne
# klareres og autoriseres i tråd med sikkerhetslovens bestemmelser", "må kunne
# autoriseres for ... begrenset", Nynorsk "klarerast/autoriserast ... sikkerheitslova",
# bare bullets ("Kunne sikkerhetsklareres på nivå hemmelig", "kan sikkerhetsklareres"),
# "(du) må sikkerhetsklareres og autoriseres på nivå hemmelig", "for stillingen må
# sikkerhetsklarering påregnes" and "krever at du kan autoriseres". So instead of one
# more phrasing this judges every clearance STEM mention on its own clause: it counts
# when the clause carries a requirement cue (må/krav/forutsetning/kreves/skal være/...)
# or is a bare eligibility bullet ("kan/kunne sikkerhetsklareres"), and not when it is
# hedged ("kan bli aktuelt", "mulighet for", "dersom", "enkelte stillinger", English
# "may"), negated/softened (_match_is_firm) or merely informational. The bare verbs
# klareres/autoriseres only count with sikkerhetsloven/hemmelig/begrenset/NATO nearby,
# so IT-access "autoriseres for tilgang" and customs "tollklarering" never match.
_CLEARANCE_STEM_RE = re.compile(
    r"(?:sikkerhets?|trygging(?:s)?|tryggleiks?|sikkerheits?)klar\w*|"
    r"\bsecurity[- ]clearance|\bsecurity vetting|\bnato secret|"
    r"\bklarer(?:es|ast|t)\b|\bklarering\b|\bautoriser(?:es|ast|t)\b|\bautorisasjon\b"
)
# The stem alternatives that name a clearance on their own (no law context needed).
_CLEARANCE_SELF_EVIDENT_RE = re.compile(r"^(?:(?:sikkerhets?|trygging(?:s)?|tryggleiks?|sikkerheits?)klar|security|nato)")
_CLEARANCE_LAW_RE = re.compile(
    r"sikkerhetslov|sikkerheitslov|tryggingslov|tryggleikslov|sikkerhetsklar|tryggingsklar|hemmelig|hemmeleg|begrenset|avgrensa|"
    r"konfidensiell|\bnato\b|\bnsm\b|nasjonal sikkerhetsmyndighet|klareringsmyndighet|skjermingsverdig|gradert"
)
_CLEARANCE_CUE_RE = re.compile(
    r"\bmå\b|\bmust\b|\bkrav\b|\bkravet\b|\bkravene\b|\bkreves\b|\bkrever\b|\bkrevst\b|\bkrevast\b|"
    r"forutsetning|forutsett|vilkår|\bpåkrevd|obligatorisk|påregnes|\bskal\s+(?:\w+\s+){0,3}(?:være|bli|kunne)\b|"
    r"\brequire[sd]?\b|\brequirements?\b|\bmandatory\b|\bprerequisite\b|\bhave to\b|\bneed to\b|\bshall\b|"
    r"\beligible\b|\bsatisfy\b|\bobtain\b|\bhold(?:s|ing)?\b|\bheld\b|\bundergo\b|\bnecessary\b"
)
_CLEARANCE_ELIGIBILITY_RE = re.compile(
    r"(?:\bkunne|\bkan|\bkunna|\bevne til å|\bi stand til å|\bkvalifisert for å|\bkvalifisere (?:til|for))\s+"
    r"(?:bli\s+|verte\s+)?(?:\w+\s+)?(?:klarer|klarert|autoriser|sikkerhets?klar|tryggingsklar|sikkerheitsklar)"
)
_CLEARANCE_HEDGE_RE = re.compile(
    r"\bkan\b[^.\n]{0,30}\b(?:aktuelt|aktuell|nødvendig|påkrevd|krevd|stilt|stilles|kreves|et krav)\b|"
    r"\bkan\s+(?:også\s+)?(?:kreve|stille|medføre|innebære|måtte)\b|\bvil\s+kunne\b|\bkunne\s+(?:bli\s+)?(?:kreve|stille)\b|"
    r"\bkan\s+det\b|\beventuell|\bev\.|\bmulighet(?:en)?\s+(?:for|til)\b|\bdersom\b|\bhvis\b|\bom\s+nødvendig\b|"
    r"\bved\s+behov\b|\bnår\s+(?:det\s+er\s+)?relevant|\benkelte\b|\bnoen\s+(?:av\s+)?(?:våre\s+)?stilling|"
    r"\bbør\b|\bhelst\b|\bsenere\s+tidspunkt|\bisps\b|\bflyplass|\blufthavn|\bairport|\band/or\b|"
    r"\bmay\b|\bmight\b|\bcould\b|\bcan\s+be\b|\bif\s+(?:required|necessary|needed|relevant|applicable)\b|"
    r"\bwhere\s+(?:required|necessary|relevant|applicable)\b|\bdepending\b|\bcertain\b|\bsome\b|\bpossible\b|\bpotential"
)


_CLEARANCE_NEGATED_AFTER_RE = re.compile(r"\b(?:ikke|ikkje|not|no longer)\b")


def _section_before(text: str, pos: int):
    """'req'/'opt'/None - the requirement/optional heading in force at `pos`
    (same heading vocabulary as iter_requirement_clauses, which can't be keyed by offset)."""
    # Only a heading within the last 8 non-empty lines counts: an unrecognised heading further
    # down ("Vi ser etter deg som har følgende kvalifikasjoner:") must not leave us in the
    # "Om oss:" section opened a screen above (it made real requirements read as optional).
    section, since = None, 0
    for line in text[:pos].split("\n")[:-1]:
        line = line.strip()
        if not line:
            continue
        since += 1
        if OPTIONAL_HEADING_RE.match(line):
            section, since = "opt", 0
        elif REQUIREMENT_HEADING_RE.match(line):
            section, since = "req", 0
    return section if since <= 8 else None


def _clearance_mention_is_firm(text: str, m: "re.Match[str]") -> bool:
    cs, ce = _clause_bounds(text, m.start(), m.end())
    clause = text[cs:ce]
    if not _CLEARANCE_SELF_EVIDENT_RE.match(m.group()) and not _CLEARANCE_LAW_RE.search(text[max(0, cs - 100):ce]):
        return False
    if _SOME_POSITIONS_RE.search(text[max(0, m.start() - 80):m.start()]):
        return False
    if _CLEARANCE_HEDGE_RE.search(clause) or not _match_is_firm(text, m):
        return False
    if _CLEARANCE_NEGATED_AFTER_RE.search(text[m.end():ce]):  # "Sikkerhetsklarering er ikke påkrevd."
        return False
    if _CLEARANCE_CUE_RE.search(clause):
        return True
    # No verb of obligation: a short bullet ("Kunne sikkerhetsklareres på nivå hemmelig",
    # "kan sikkerhetsklareres") or a bare noun bullet ("Sikkerhetsklarering (nivå
    # "konfidensielt") for tilsetting ...") counts unless it sits under an optional heading.
    ls = text.rfind("\n", 0, m.start()) + 1
    line = text[ls:text.find("\n", m.end()) if "\n" in text[m.end():] else len(text)].strip()
    bare_bullet = len(line) <= 120 and not line.endswith(":") and re.match(r"\W*(?:sikkerhets?klar|tryggingsklar|security[- ]clearance)", line)
    if (_CLEARANCE_ELIGIBILITY_RE.search(clause) and len(clause) <= 160) or bare_bullet:
        return _section_before(text, m.start()) != "opt"
    return False


def _has_clearance_requirement_by_stem(text: str) -> bool:
    return any(_clearance_mention_is_firm(text, m) for m in _CLEARANCE_STEM_RE.finditer(text))


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
    r"ønsket kompetanse|fordelaktig|det er en fordel(?:\s+om du(?:\s+også)?(?:\s+har)?)?|vi ser gjerne|"
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
        # Optional first: REQUIREMENT_HEADING_RE's optional prefix word swallows "Ønskede
        # kvalifikasjoner" / "Ønskelige kvalifikasjoner" as a requirements heading, so every
        # bullet under them used to read as REQUIRED (corpus audit 2026-11-17).
        if OPTIONAL_HEADING_RE.match(line):
            section = "opt"
            continue
        if REQUIREMENT_HEADING_RE.match(line):
            section = "req"
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
# Degree-style equivalence alternatives ("X eller tilsvarende"). They soften a
# FORMAL-QUALIFICATION requirement (a degree OR equivalent is still a
# requirement of something) but must not cancel a years/management/fluency
# penalty: "minimum 3 års erfaring fra lager eller tilsvarende" still asks
# for 3 years (review 2026-10-05). has_optional_marker(include_equivalence=
# False) leaves them out.
_EQUIVALENCE_RE = re.compile(
    r"eller tilsvarende|eller tilsvarande|eller liknende|eller lignende|"
    r"eller realkompetanse|eller relevant erfaring|eller erfaring|eller lang erfaring|"
    r"or equivalent"
)
# A softener anywhere in the clause wins even under a requirements heading
# ("Kvalifikasjoner: ... truckførerbevis er en fordel, men ikke et krav" —
# measured live, ~55 of 118 truckfør-mentioning ads use exactly this shape).
_OPTIONAL_MARKER_CORE_RE = re.compile(
    # "fordel" used to be a bare substring, so "Vi tilbyr gode fordeler"
    # (benefits) and "fordelt på" (distributed) softened any requirement in
    # the same clause (2026-10-05, /fullreview deep). Now only the singular
    # advantage: "fordel", "fordelen", "fordelaktig(e)", and compounds
    # ("konkurransefordel") — right-anchored, no left \b on purpose.
    r"gjerne|ønskelig|ønskjeleg|fordel(?:en|aktig\w*)?\b|pluss\b|positivt|"
    r"ikke\s+(?:\w+\s+)?krav|ikkje\s+(?:\w+\s+)?krav|ikke en forutsetning|"
    r"ikke noe must|bør ha|manglar du|mangler du|ikke nødvendig|kjekt om|"
    r"et ønske|kan veie opp|kan kompensere|"
    r"an advantage|considered an advantage|is a plus|preferred\b|"
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
OPTIONAL_MARKER_RE = re.compile(f"(?:{_OPTIONAL_MARKER_CORE_RE.pattern})|(?:{_EQUIVALENCE_RE.pattern})")
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


def has_optional_marker(clause: str, include_equivalence: bool = True) -> bool:
    """Is this clause softened as a whole? Ignores softeners that only
    qualify a trailing or parenthesised detail. Shared by every soft/hard
    decision (hard_blocks' truckfør/forklift checks and scoring.py's
    car/formal-qualification/programming checks) so the scoping rule can't
    drift between them — it used to live inline in one of the five.
    `include_equivalence=False` ignores "eller tilsvarende"-style
    alternatives (see _EQUIVALENCE_RE) — used by the years/management/fluency
    penalties, where an equivalence does not make the requirement optional."""
    scoped = _TRAILING_QUALIFIER_RE.sub("", _PARENS_RE.sub(" ", clause))
    return bool((OPTIONAL_MARKER_RE if include_equivalence else _OPTIONAL_MARKER_CORE_RE).search(scoped))


# Boundaries between independent claims inside one clause. A softener only
# softens the claim it sits in: "Du må kunne sikkerhetsklareres for hemmelig
# og det er en fordel med erfaring fra Forsvaret" requires the clearance and
# merely prefers the experience (review 2026-10-05).
_SEGMENT_SPLIT_RE = re.compile(r",|;|\bog\b|\bmen\b|\bbut\b|\band\b")


def has_optional_marker_near(clause: str, start: int, end: int, include_equivalence: bool = True) -> bool:
    """has_optional_marker restricted to the sub-segment of `clause` (split
    on , ; og men but and) that contains the span [start, end). Parenthesised
    text is blanked first (same length, so offsets hold) so a comma inside
    parentheses doesn't cut a segment in half."""
    blanked = _PARENS_RE.sub(lambda m: " " * len(m.group()), clause)
    seg_start, seg_end = 0, len(blanked)
    for cut in _SEGMENT_SPLIT_RE.finditer(blanked):
        if cut.end() <= start:
            seg_start = cut.end()
        elif cut.start() >= end:
            seg_end = cut.start()
            break
    return has_optional_marker(blanked[seg_start:seg_end], include_equivalence)


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


# Negation in the text BEFORE a mention, within its own clause segment, but
# only the explicit "no requirement" phrasings that GOVERN the requirement
# ("Det stilles ikke krav til X", "Ingen krav om X", "Du trenger ikke å
# inneha X"). A bare ikke/uten/ingen is not enough: "Søkere uten norsk
# autorisasjon vil ikke bli vurdert" and "Det er ikke mulig å tiltre
# stillingen uten autorisasjon" are FIRM requirements (double negatives,
# review 2026-10-05). Only the segment after the last comma / "men" / "but"
# counts, so "Stillingen er ikke deltid, men krever X" stays firm.
_NEGATION_BEFORE_RE = re.compile(
    r"\bikke\s+(?:et\s+|noe\s+)?krav|\bikkje\s+(?:et\s+|noko\s+|noe\s+)?krav|"
    r"\bingen\s+(?:\w+\s+)?krav|\bintet\s+krav|\buten\s+krav|\bstilles\s+ikke|"
    r"\b(?:vil|skal)\s+ikke\s+kreve|\bkrever\s+ikke|\bkreves\s+ikke|"
    r"\btrenger\s+ikke|\btrengs\s+ikke|\bikke\s+nødvendig|\bikke\s+påkrevd"
)
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
    softener in its own sub-segment (has_optional_marker_near: "en fordel",
    "ønskelig, men ikke et krav")
    or the match is negated. Added 2026-10-05 (/fullreview deep) — the
    body-level clearance/authorisation/EU-passport checks used to be bare
    regex hits, blind to "ikke krav om ...", "en fordel med ...", "No EU
    passport is required"."""
    cs, ce = _clause_bounds(text, m.start(), m.end())
    # Only a softener in the match's OWN sub-segment counts (see
    # has_optional_marker_near).
    if has_optional_marker_near(text[cs:ce], m.start() - cs, m.end() - cs):
        return False
    before = _NEGATION_SEGMENT_SPLIT_RE.split(text[cs:m.start()])[-1]
    # The phrase may run INTO the match ("Ingen krav om X": the match starts
    # at "krav"), so search before + matched text, keeping hits that begin
    # before the match.
    if any(n.start() < len(before) for n in _NEGATION_BEFORE_RE.finditer(before + text[m.start():m.end()])):
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


def _clauses_if_mentioned(mention_re, body_l: str) -> list:
    """iter_requirement_clauses(body_l) as a list - or [] when the mention
    pattern occurs nowhere in body_l (a clause is a substring of the body, so
    it cannot match either; _has_unmet_requirement then sees no mention index
    exactly as it would with the full list). Saves the split for the ~95% of
    ads that never name a truck certificate."""
    return list(iter_requirement_clauses(body_l)) if mention_re.search(body_l) else []


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
    clauses = _clauses_if_mentioned(TRUCKFORERBEVIS_MENTION_RE, body_l)
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
    clauses = _clauses_if_mentioned(FORKLIFT_CERT_MENTION_RE, body_l)
    return _has_unmet_requirement(FORKLIFT_CERT_MENTION_RE, clauses, title_l=title_l)


# Below this and outside Vestland, relocating doesn't cover rent — see
# PLAN.md point 4 ("щоб при переїзді можна було реально зняти хату/кімнату/
# купити їжи"). Only applied when extent_percent is actually known (parsed
# from title/description/jobScope) — an unresolved percentage is NOT treated
# as failing this check, per the user's own steer toward "не вгадаєш" (don't
# hide what we can't confidently judge).
LOW_EXTENT_FAR_THRESHOLD = 60

# TEMPORARY (user decision 2026-10-27, "поки що"): every vacancy located on
# Svalbard is hidden. The user holds a 5-year permit on the mainland;
# utlendingsloven does not apply on Svalbard, so years there do not count
# toward permanent residence/citizenship; Svalbard is outside Schengen
# (document check on the flight, and his travel document has not arrived
# yet); the introduction programme and free Norwegian course are tied to the
# mainland municipality. Revisit once he has his reisebevis and permanent
# residence is no longer the priority. Signal = structured location only:
# county (NAV 'SVALBARD', Jobbnorge 'Svalbard') or, for sources that give no
# county (finn/LinkedIn digests), the municipal. Never the description:
# mainland ads mention "kunder på Svalbard" (27 live rows) and must stay.
SVALBARD_LOCALITIES = frozenset({"svalbard", "longyearbyen", "ny-ålesund", "barentsburg", "svea"})
SVALBARD_REASON = "Шпіцберген: роки там не йдуть у ПМЖ, поза Шенгеном, без інтропрограми (тимчасово)"

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
        for m in _compile(pattern).finditer(body_l):
            if _SOME_POSITIONS_RE.search(body_l[max(0, m.start() - 80):m.start()]):
                continue
            if not _match_is_firm(body_l, m):
                continue
            if not generic:
                # Corpus audit 2026-11-17: a softener in the rest of the same line, and a bullet
                # under an "ønskede kvalifikasjoner"-style heading that carries no hard verb.
                cs, ce = _clause_bounds(body_l, m.start(), m.end())
                if _AUTH_ALTERNATIVE_BEFORE_RE.search(body_l[cs:m.start()]):
                    continue
                if re.search(r"fortrinnsvis|primært|\bhelst\b|gjerne|foretrekk", body_l[cs:m.start()]):
                    continue
                # Rest of the same line (a softener LEFT of the match is handled above). Not the next
                # line: "Norsk autorisasjon som sykepleier\nAutoriserte helsefagarbeidere ... oppfordres
                # til å søke" softens a different group, not the sykepleier requirement.
                line_end = body_l.find("\n", m.end())
                rest = body_l[m.end():line_end if line_end != -1 else len(body_l)][:300]
                soft = _AUTH_SOFT_AROUND_RE.search(rest)
                # "(3. års sykepleierstudenter kan også søke)" opens the door to students, which he is not.
                if soft and not re.search(r"student|nyutdann|under utdanning|studerer|venter på", rest[max(0, soft.start() - 60):soft.end() + 30]):
                    continue
                if _section_before(body_l, m.start()) == "opt" and not REQUIREMENT_VERB_RE.search(body_l[cs:ce]):
                    continue
            if generic:
                cs, ce = _clause_bounds(body_l, m.start(), m.end())
                if "tilgang" in body_l[cs:ce]:
                    continue
                if not _HEALTH_CONTEXT_RE.search(body_l[max(0, m.start() - 150):m.end() + 150]):
                    continue
            return True
    return False


# --- Corpus audit 2026-11-17: other regulated licences stated as a requirement in the BODY ----
# The title lists above only fire when the job IS the profession. These ads carry an
# unrelated title ("Rådgiver", "Seniorkonsulent", "Etterforsker PB3/2/1") but state the
# licence as a qualification: "statsautorisert regnskapsfører" x14 (Aspia, Bemanningsxperten),
# "advokatbevilling", "lønnes som politibetjent 3/2/1" / "bestått vitnemål fra
# politihøgskole/politiskole", maritime "dekksoffisersertifikat D5", DSB electrician approval,
# and school posts ("rektor", "undervisningsinspektør") that need "godkjent pedagogisk utdanning".
# Each is judged like the health bullets: firm clause, no softener/alternative around it.
REGULATED_BODY_PATTERNS = [
    (r"\b(?:statsautorisert|statsautoriserte|registrert|registrerte)\s+(?:revisor|revisorer|regnskapsfører|regnskapsførere|"
     r"rekneskapsførar|regnskapfører)\b|"
     r"\bautorisert\s+(?:revisor|regnskapsfører|rekneskapsførar)\b|"
     r"\b(?:norsk\s+)?(?:autorisasjon|bevilling)\s+som\s+(?:revisor|regnskapsfører|rekneskapsførar|advokat)\b|"
     r"\b(?:norsk\s+)?advokatbevilling\b", "Регульована професія (право/аудит)"),
    (r"\b(?:lønn(?:es|a|et|t)|avlønnes)\s+som\s+politi(?:betjent|overbetjent|førstebetjent|inspektør)\b|"
     r"\b(?:vitnemål|eksamen|utdanning)\s+fra\s+politihøgskole(?:n)?(?:\s*/\s*politiskole(?:n)?)?\b",
     "Потрібна освіта Politihøgskolen (норвезька поліцейська академія)"),
    (r"\b(?:dekks|maskin)offiser(?:s)?-?sertifikat\w*|\bd[1-5]-?sertifikat\b|\bsertifikat\s+d[1-5]\b|"
     r"\b(?:dekks|maskin)offiser\b[^.\n]{0,30}\bsertifikat\b|\bgyldig\s+stcw\b", "Потрібні права / морський сертифікат"),
    (r"\bgodkjenning\s+fra\s+dsb\s+som\s+elektroinstallatør|\bbestått\s+installatørprøven|\bautorisasjon\s+fra\s+dsb\b|"
     r"\bfagansvarlig\s+for\s+elvirksomhet", "Потрібна авторизація DSB (електромонтажні роботи)"),
    (r"\b(?:har|må\s+ha|krav\s+om|krever|kreves|forutsetter|forutsetning)\s+(?:et\s+|en\s+)?(?:godkjent|godkjend)\s+"
     r"(?:faglig\s+og\s+)?(?:pedagogisk|lærer|lærar)\w*(?:\s+utdann\w+)?|"
     r"\bkrav\s+om\s+(?:godkjent\s+|godkjend\s+)?(?:lærerutdanning|lærarutdanning)|"
     r"\bundervisningskompetanse\s+er\s+en\s+forutsetning|"
     r"\b(?:må\s+ha|krever|krav\s+om)\s+(?:godkjent\s+)?undervisningskompetanse",
     "Потрібна норвезька педагогічна освіта"),
]
# Teaching text in an ad that also hires assistants/unskilled ("Tilkallingsvikarer fagarbeidere/
# assistenter") is a conditional sentence about the teacher posts, not a requirement of every post.
_MIXED_STAFF_TITLE_RE = re.compile(r"assistent|fagarbeid|ufaglært|miljø|barne- og ungdomsarbeid|renhold")
_REGULATED_ALTERNATIVE_AFTER_RE = re.compile(r"^\s*(?:eller|/|og/eller)\b")


def _regulated_licence_reason(title_l: str, body_l: str):
    for pattern, reason in REGULATED_BODY_PATTERNS:
        if reason.endswith("педагогічна освіта") and _MIXED_STAFF_TITLE_RE.search(title_l):
            continue
        firm = soft = False
        for m in _compile(pattern).finditer(body_l):
            if _SOME_POSITIONS_RE.search(body_l[max(0, m.start() - 80):m.start()]):
                continue
            cs, ce = _clause_bounds(body_l, m.start(), m.end())
            before, after = body_l[cs:m.start()], body_l[m.end():ce]
            if _REGULATED_ALTERNATIVE_AFTER_RE.match(after) or _AUTH_ALTERNATIVE_BEFORE_RE.search(before):
                continue  # an alternative, neither a requirement nor a statement that it is optional
            if (
                not _match_is_firm(body_l, m)
                or re.search(r"fortrinnsvis|primært|\bhelst\b|gjerne|foretrekk|\bfor\s+(?:stilling|lærerstilling|undervisning)", before)
                or _AUTH_SOFT_AROUND_RE.search(after)
                or re.search(r"\beller\s+(?!høyere|høgare|bedre)", after[:40])
                or (_section_before(body_l, m.start()) == "opt" and not REQUIREMENT_VERB_RE.search(body_l[cs:ce]))
            ):
                soft = True
            else:
                firm = True
        # One explicitly optional mention ("Er du statsautorisert, er det et pluss, men ikke et krav")
        # means the other mentions are not a hard requirement either.
        if firm and not soft:
            return reason
    return None


# --- Corpus audit 2026-11-17: a foreign language demanded in the BODY ------------------------
# The title rule above only knows "svensktalende". Ads state it as a bullet: "Fluent in German",
# "Meget gode kunnskaper i svensk og engelsk", "Du må snakke polsk og engelsk", "Language: German
# and English - only apply if you speak German fluently" (German-speaking tour guides, Voss).
# Counts only with a plain requirement wording and NO alternative in the clause: any
# "eller/or/slash/and-or" ("norsk, svensk eller dansk", "Norwegian or Swedish") means the user's
# own language qualifies, and "gode kunnskaper i X er en fordel/plus" is a preference.
_FOREIGN_LANG_ANY = (
    r"(?:svensk|dansk|finsk|tysk|fransk|nederlandsk|spansk|italiensk|polsk|islandsk|portugisisk|swedish|danish|finnish|"
    r"german|french|dutch|spanish|italian|polish|icelandic|portuguese|lithuanian|latvian|estonian|czech|hungarian|"
    r"romanian|bulgarian|greek|turkish|arabic|chinese|mandarin|japanese|korean)"
)
_FOREIGN_LANG_REQUIRED_RE = re.compile(
    r"(?:\b(?:må|skal|bør)\s+(?:\w+\s+){0,3}?(?:beherske|snakke|kunne\s+snakke|ha)\s+(?:\w+\s+){0,2}?" + _FOREIGN_LANG_ANY + r"\w*|"
    r"\b(?:flytende|fluent(?:\s+in)?|fluency\s+in|meget\s+gode|svært\s+gode|veldig\s+gode|very\s+good|excellent|native)\s+"
    r"(?:(?:\w+\s+){0,2}?(?:kunnskaper|ferdigheter|knowledge|skills)\s+(?:i|på|of|in)\s+)?" + _FOREIGN_LANG_ANY + r"\w*|"
    r"\b" + _FOREIGN_LANG_ANY + r"kunnskaper|"
    r"\bonly\s+apply\s+if\s+you\s+speak\s+" + _FOREIGN_LANG_ANY + r"\b|"
    r"\b(?:gode|good)\s+(?:ferdigheter|language\s+skills|skills)\s+(?:i|på|in)\s+" + _FOREIGN_LANG_ANY + r"\b)"
)
_FOREIGN_LANG_ALTERNATIVE_RE = re.compile(
    r"\beller\b|\bor\b|/|\band/or\b|\bog/eller\b|alternativ|\bevt\b|eventuelt|\bplus\b|fordel|advantage|bonus|preferred|"
    r"ønskelig|ønskjeleg|gjerne|forstå|understand|\bkan\s+også\b|desirable|nice to have|\bbør\b"
)
_NORDIC_CONTEXT_RE = re.compile(r"\bnorsk\b|\bnorwegian\b|\bskandinavisk|\bscandinavian\b|\bnordisk")


def _has_foreign_language_requirement(body_l: str) -> bool:
    for m in _FOREIGN_LANG_REQUIRED_RE.finditer(body_l):
        cs, ce = _clause_bounds(body_l, m.start(), m.end())
        clause = body_l[cs:ce]
        if _FOREIGN_LANG_ALTERNATIVE_RE.search(clause) or not _match_is_firm(body_l, m):
            continue
        # "norsk og svensk" bullets: the user can read the Norwegian half only, but a clause that
        # already names a Scandinavian/Nordic language is the "any Nordic language" family - skip.
        if _NORDIC_CONTEXT_RE.search(clause) and not re.search(r"\bog\b|\band\b", clause):
            continue
        if _section_before(body_l, m.start()) == "opt" and not REQUIREMENT_VERB_RE.search(clause):
            continue
        return True
    return False


def check_exclusion(
    title: str | None,
    description_text: str | None,
    county: str | None = None,
    extent_percent: int | None = None,
    municipal: str | None = None,
) -> tuple[bool, str | None]:
    """Returns (is_excluded, human-readable reason in Ukrainian)."""
    title_l = normalize_text(title).lower()

    if any(normalize_text(v).lower() in SVALBARD_LOCALITIES for v in (county, municipal) if v):
        return True, SVALBARD_REASON

    for _key, patterns, reason in BLOCK_CATEGORIES:
        for pattern in patterns:
            if _compile(pattern).search(title_l):
                return True, reason

    if (
        county
        and county.strip().upper() != "VESTLAND"
        and extent_percent is not None
        and extent_percent < LOW_EXTENT_FAR_THRESHOLD
    ):
        return True, f"Поза Vestland і лише {extent_percent}% ставки — переїзд економічно нереальний"

    body_l = normalize_text(description_text).lower()
    if _has_body_authorisation_requirement(body_l):
        return True, "В описі прямо вимагається норвезька авторизація"

    regulated_reason = _regulated_licence_reason(title_l, body_l)
    if regulated_reason:
        return True, regulated_reason

    if _has_foreign_language_requirement(body_l):
        return True, "Потрібна мова, якою я не володію (шведська/данська/німецька/польська тощо)"

    if (
        _has_definite_security_clearance_requirement(body_l)
        or _has_definite_security_clearance_requirement_en(body_l)
        or _has_clearance_requirement_by_stem(body_l)
    ):
        return True, "Потрібен допуск до державної таємниці (sikkerhetsklarering) — недосяжно без громадянства"

    if _has_norwegian_citizenship_only_requirement(body_l):
        return True, NORWEGIAN_CITIZENSHIP_ONLY_REASON

    if _has_eu_passport_requirement(body_l):
        return True, "Вакансія фізично за кордоном (вимагає EU passport), не в Норвегії"

    if _has_unmet_truckforerbevis_requirement(title_l, body_l):
        return True, "Вимагає truckførerbevis без навчання на місці — поки не отримуємо"

    if _has_unmet_forklift_certificate_requirement(title_l, body_l):
        return True, "Вимагає forklift certificate без навчання на місці — поки не отримуємо"

    return False, None
