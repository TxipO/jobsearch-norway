"""Canonical structured profile data — the single source of truth the CV
builder assembles from. Kept as Python data (not parsed from profile.md)
because these facts change rarely and only by deliberate edit; parsing the
human-readable markdown would be fragile. profile.md stays the narrative
reference for humans and for scoring; this is the machine-readable version
for document generation.

Every JOBS entry has a stable `id` so a per-vacancy tailoring step can say
"put these in Relevant erfaring, those in Annen erfaring" without the model
ever rewriting the underlying facts. English throughout — the CV is written
in English (candidate's honest strongest language; see cv-reference.md).

Norwegian variant (2026-07-17): cv-reference.md itself recommends having
both an English and a Norwegian CV for a foreign applicant, sent depending
on the posting's language. Every user-facing string has a matching `_no`
field alongside it (title_no, description_no, etc.) — proper nouns that
don't translate (company names, institution names, dates) are shared
between both languages and have no `_no` counterpart. Same honesty rule
applies to the Norwegian text as the English: no claim without a fact
behind it, no self-undermining language, no invented soft skills.
"""

# Contact/hobby data lives in personal.json (gitignored real data), NOT here.

JOBS = {
    "crypto_teaching": {
        "title": "Financial Literacy & Blockchain Instructor",
        # Compact compound form, not the literal translation ("Instruktør i
        # finansiell kompetanse og blokkjedeteknologi") — that version was
        # 71 chars combined with the company name, over the 65-char tab-stop
        # cap that caused a real rendering bug earlier this session.
        "title_no": "Finans- og blokkjedeinstruktør",
        "company": "Self-employed",
        "location": "Remote",
        "location_no": "Fjernarbeid",
        "dates": "Nov 2024 – present",
        "description": (
            "Teach private courses and a paid subscriber community on blockchain-based "
            "finance and investing. Develop course materials and break down technical "
            "topics into plain language for a non-technical audience, with ongoing remote "
            "support for participants."
        ),
        "description_no": (
            "Underviser i private kurs og et betalt abonnementsfellesskap om "
            "blokkjedebasert finans og investering. Utvikler kursmateriell og forklarer "
            "tekniske temaer på et enkelt språk for et ikke-teknisk publikum, med løpende "
            "support til deltakerne."
        ),
    },
    "selfemployed_repair": {
        "title": "Computer & Laptop Repair Technician",
        "title_no": "Reparatør av PC og bærbare datamaskiner",
        "company": "Self-employed",
        "location": "Ukraine",
        "location_no": "Ukraina",
        "dates": "May 2022 – Oct 2024",
        # Expanded 2026-07-20 (user-confirmed real facts, filling out an
        # actual Jobbnorge application surfaced these — printers, remote
        # support): every added detail was explicitly confirmed by the user
        # before being written here, not inferred.
        "description": (
            "Diagnosed and repaired laptops and desktop PCs for individual clients. "
            "Troubleshot both hardware and software issues, installed and configured "
            "operating systems (Windows) and software, and performed hardware maintenance "
            "and cleaning. Set up and troubleshot printers and printing for clients, and "
            "provided remote support for simpler software and configuration issues. "
            "Explained technical issues in plain language to clients without an IT "
            "background, and followed up each case through to resolution."
        ),
        "description_no": (
            "Diagnostiserte og reparerte bærbare og stasjonære datamaskiner for "
            "privatkunder. Feilsøkte på både maskinvare og programvare, installerte og "
            "konfigurerte operativsystemer (Windows) og programvare, og utførte "
            "vedlikehold og rengjøring av maskinvare. Satte opp og feilsøkte skrivere og "
            "utskriftsløsninger for kunder, og ga fjernhjelp (remote) ved enklere "
            "programvare- og konfigurasjonssaker. Formidlet teknisk informasjon på en "
            "forståelig måte til kunder uten IT-bakgrunn, og fulgte hver sak opp til den "
            "var løst."
        ),
    },
    "pumb": {
        "title": "Technical Support Specialist",
        "title_no": "Teknisk supportmedarbeider",
        # Kept short deliberately: a long title+company string pushes past
        # the right-aligned date tab stop and collapses the gap between
        # them (caught live in the rendered PDF, 2026-07-17).
        "company": "FUIB (PUMB)",
        "location": "Ukraine",
        "location_no": "Ukraina",
        "dates": "Feb 2021 – May 2022",
        # Expanded 2026-07-20 (user-confirmed): M365 admin, AD/Entra ID
        # account management, Jira ticketing, printers, AV/meeting-room
        # equipment, and work-report documentation were all explicitly
        # confirmed by the user as real, not inferred from the job title.
        "description": (
            "Provided internal 1st/2nd-line technical support to colleagues across the "
            "head office and branch locations, both on-site and remotely. Managed user "
            "accounts in Active Directory / Microsoft Entra ID — creating and disabling "
            "accounts, resetting passwords, and managing access rights and groups. "
            "Administered and configured Microsoft 365 accounts and services for users via "
            "the admin center. Troubleshot and repaired hardware, reinstalled operating "
            "systems (Windows) and software, and set up and troubleshot printers/printing "
            "and AV/meeting-room equipment. Logged, processed and followed up support "
            "requests in Jira, and completed documentation and reports for work performed."
        ),
        "description_no": (
            "Ga intern brukerstøtte (1. og 2. linje) til kolleger på tvers av hovedkontor "
            "og filialer, både på stedet og via fjernhjelp (remote). Administrerte "
            "brukerkontoer i Active Directory / Microsoft Entra ID – opprettet og sperret "
            "kontoer, tilbakestilte passord og styrte tilganger og grupper. Administrerte "
            "og konfigurerte Microsoft 365-kontoer og -tjenester for brukerne via "
            "administrasjonssenteret. Feilsøkte og reparerte maskinvare, installerte "
            "operativsystemer (Windows) og programvare på nytt, og satte opp og feilsøkte "
            "skrivere/utskriftsløsninger og AV-/møteromsutstyr. Registrerte, behandlet og "
            "fulgte opp henvendelser i Jira, og fylte ut dokumentasjon og rapporter for "
            "utført arbeid."
        ),
    },
    "verna": {
        "title": "Field Technician",
        "title_no": "Feltteknikker",
        "company": "Verna",
        "location": "Ukraine",
        "location_no": "Ukraina",
        "dates": "Jul 2020 – Feb 2021",
        # Expanded 2026-07-20 (user-confirmed): work-report documentation
        # added per user's confirmation, same as pumb.
        "description": (
            "Replaced and installed equipment on-site for clients, transported and set up "
            "hardware for clients across the city, and performed routine technical "
            "maintenance. Documented and reported completed work, filling out work reports "
            "for each assignment."
        ),
        "description_no": (
            "Byttet og installerte utstyr hos kunder på stedet, transporterte og satte opp "
            "maskinvare for kunder i hele byen, og utførte rutinemessig teknisk "
            "vedlikehold. Dokumenterte og rapporterte utført arbeid, og fylte ut "
            "arbeidsrapporter for hvert oppdrag."
        ),
    },
    "miniso": {
        "title": "Sales Assistant",
        "title_no": "Butikkmedarbeider",
        "company": "Miniso",
        "location": "Ukraine",
        "location_no": "Ukraina",
        "dates": "Jan 2020 – Jun 2020",
        "description": "Customer-facing retail role with daily direct interaction with the public.",
        "description_no": "Kundevendt butikkrolle med daglig direkte kontakt med publikum.",
    },
    "callcenter": {
        "title": "Call Center Operator",
        "title_no": "Kundesenteroperatør",
        "company": "Utilities sector",
        "location": "Ukraine",
        "location_no": "Ukraina",
        "dates": "Feb 2017 – May 2017",  # confirmed 2026-07-20, was bare "2017"
        "description": "Handled inbound customer enquiries by phone. Part-time, 50%.",
        "description_no": "Behandlet innkommende kundehenvendelser på telefon. Deltid, 50%.",
    },
}

# Default split when a tailoring step doesn't override it. IT-support framing:
# real IT experience up top, everything else below. For a retail/customer-
# service vacancy the tailoring flips miniso/callcenter up into relevant.
# Within each list: reverse-chronological (most recent first), standard CV
# convention. verna (May 2021 – Nov 2021) precedes pumb (Nov 2021 – May
# 2022) — confirmed by user 2026-07-20, no gap/overlap left unresolved.
DEFAULT_RELEVANT = ["selfemployed_repair", "pumb", "verna"]
DEFAULT_OTHER = ["crypto_teaching", "miniso", "callcenter"]

# Reverse-chronological across ALL jobs, no relevant/other split — for the
# general-purpose CV (retail/warehouse/broad applications). Same "verna"
# placement caveat as above.
ALL_JOBS_CHRONOLOGICAL = [
    "crypto_teaching", "selfemployed_repair", "pumb", "verna", "miniso", "callcenter",
]

EDUCATION = [
    {
        "degree": "Bachelor's degree (Bakalavr), Cyber Security",
        "degree_no": "Bachelorgrad (Bakalavr), cybersikkerhet",
        # Institution names are proper nouns — kept in their original form
        # in both languages rather than inventing a Norwegian institution
        # name that doesn't officially exist.
        "institution": "State University of Telecommunications, Kyiv, Ukraine",
        "dates": "2022",
        # Norwegian equivalent, per HK-dir automatisk godkjenning — NOT a
        # "not recognized" disclaimer. See profile.md education section.
        # 240 ECTS / 4-year program confirmed 2026-07-17 — this specific
        # duration is what qualifies for full bachelorgrad equivalence per
        # HK-dir's table (a 3-year Bakalavr would only equate to
        # høgskolekandidatgrad, a 2-year level).
        "note": "240 ECTS. Norwegian equivalent: bachelorgrad (HK-dir automatisk godkjenning)",
        "note_no": "240 studiepoeng. Norsk ekvivalent: bachelorgrad (automatisk godkjenning fra HK-dir)",
    },
    {
        "degree": "College diploma (Molodshyi spetsialist), Software Development",
        "degree_no": "Fagskolediplom (Molodshyi spetsialist), programvareutvikling",
        "institution": "Kyiv State College of Tourism and Hotel Management, Ukraine",
        "dates": "2019",
        "note": "Norwegian equivalent: fagskole level (HK-dir)",
        "note_no": "Norsk ekvivalent: fagskolenivå (HK-dir)",
    },
    {
        "degree": "Python Backend Development",
        "degree_no": "Python backend-utvikling",
        "institution": "6-month bootcamp",
        "institution_no": "6 måneders kurs (bootcamp)",
        "dates": "2022",
        "note": "",
        "note_no": "",
    },
]

# Default hard-skill list for IT-support; a tailoring step can reorder/subset
# to mirror a specific vacancy's stack. Renamed from TOOLS (2026-07-17) when
# the CV grew a matching Soft Skills section — see SOFT_SKILLS below.
# "AI-assisted process automation" (not bare "AI" — too vague to mean
# anything on a skills line, and cv-reference.md's own rule against
# buzzword-without-substance applies here too) traces to a real, specific
# activity in profile.md: self-taught automation of workflows using AI
# tools, tied to the crypto-education work. Concrete claim, not hype.
HARD_SKILLS = [
    "Windows OS install & troubleshooting",
    "Microsoft 365 administration",
    "Active Directory / Entra ID (user & access management)",
    "Hardware diagnostics & repair",
    "Ticketing systems (Jira)",
    "User support / feilsøking",
    "AI-assisted process automation",
]

# Same order/meaning as HARD_SKILLS — product names (Microsoft 365, Active
# Directory / Entra ID) stay in English since that's how they're referred
# to in Norwegian IT job postings too (cv-reference.md's own keyword list
# uses the English product names verbatim).
HARD_SKILLS_NO = [
    "Installasjon og feilsøking av Windows",
    "Administrasjon av Microsoft 365",
    "Active Directory / Entra ID (bruker- og tilgangsstyring)",
    "Diagnostisering og reparasjon av maskinvare",
    "Saksbehandlingssystemer (Jira)",
    "Brukerstøtte / feilsøking",
    "KI-støttet prosessautomatisering",
]

# Measured before writing (2026-07-17): grepped ~2000 real Norwegian job
# descriptions in the local DB for common soft-skill phrases rather than
# guessing. Top hits: samarbeid/teamwork 67%, fleksibilitet 44%,
# selvstendig 44%, engasjert 37%, positiv 35%, kommunikasjon 33%,
# strukturert 32%, løsningsorientert 21%. Each entry below is chosen from
# that list AND grounded in a specific real fact from profile.md — the
# same honesty rule as HARD_SKILLS applies: no trait without a fact behind
# it, or it's just the "svulstig språk" cv-reference.md warns against.
SOFT_SKILLS = [
    "Teamwork & cross-office collaboration",  # PUMB: colleagues across office/branch locations
    "Customer-facing communication",  # retail, call center, teaching all public-facing
    "Self-directed work",  # ran own repair business solo, May 2022-Oct 2024
    "Structured follow-through",  # PUMB: reported on completed support tasks
    "Adaptability across roles",  # banking IT, retail, field tech, self-employment, teaching
    "Practical problem-solving",  # hardware diagnostics/repair is literally this
    "Fast, self-taught learner",  # self-taught AI automation and crypto/investing
]

# Same order/facts as SOFT_SKILLS. "Personlige egenskaper" is the
# established Norwegian CV term for this section — matches "personlig
# egnethet" already used in cv-reference.md's søknad guidance.
SOFT_SKILLS_NO = [
    "Samarbeid på tvers av kontorer",
    "God kommunikasjon med kunder",
    "Selvstendig arbeid",
    "Strukturert oppfølging",
    "Tilpasningsdyktig i ulike roller",
    "Praktisk problemløsning",
    "Rask og selvlært",
]

LANGUAGES = [
    ("Ukrainian", "Native"),
    ("Russian", "Professional working proficiency"),
    ("English", "Professional working proficiency"),
    ("Norwegian", "A1"),
]

# The English CV keeps the CEFR abbreviation (A1); the Norwegian one spells
# the level out ("Nybegynnernivå"), which reads more naturally to a Norwegian
# employer than a bare code. Deliberate asymmetry, not an oversight.
LANGUAGES_NO = [
    ("Ukrainsk", "Morsmål"),
    ("Russisk", "Profesjonell arbeidsevne"),
    ("Engelsk", "Profesjonell arbeidsevne"),
    ("Norsk", "Nybegynnernivå"),
]

DEFAULT_SUMMARY = (
    "IT support professional with 3+ years of hands-on technical support experience, "
    "including internal IT support for a major bank and independent computer repair work. "
    "Comfortable working directly with end users of varying technical skill and handling "
    "recurring support requests with structure and follow-through."
)

DEFAULT_SUMMARY_NO = (
    "IT-supportmedarbeider med 3+ års praktisk erfaring innen teknisk brukerstøtte, "
    "inkludert intern IT-support for en stor bank og selvstendig reparasjon av "
    "datautstyr. Komfortabel med å jobbe direkte med sluttbrukere med ulikt teknisk "
    "nivå, og håndterer gjentakende supporthenvendelser strukturert og med god "
    "oppfølging."
)

# For the general-purpose CV (retail/warehouse/broad applications) — doesn't
# lead with the IT framing, since that's not what these roles are looking
# for. Same honesty rules apply: real, transferable qualities only.
GENERAL_SUMMARY = (
    "Reliable, customer-focused professional with experience across technical support, "
    "retail, and hands-on service roles. Comfortable working directly with the public, "
    "following structured processes, and adapting quickly to new tasks and environments."
)

GENERAL_SUMMARY_NO = (
    "Pålitelig og serviceinnstilt person med erfaring fra teknisk brukerstøtte, butikk "
    "og praktiske serviceroller. Komfortabel med å jobbe direkte med kunder, følge "
    "strukturerte rutiner, og tilpasse meg raskt til nye oppgaver og arbeidsmiljøer."
)

# Role headline printed under the name in the CV header (2026-07-20,
# matching the user's own updated reference resumes) — one line naming the
# role framing, distinct per CV variant/language the same way the summary
# already is.
ROLE_HEADLINE = "IT Support Specialist"
ROLE_HEADLINE_NO = "IT-supportmedarbeider"
GENERAL_ROLE_HEADLINE = "Customer Service & Technical Support"
GENERAL_ROLE_HEADLINE_NO = "Kundeservice og teknisk support"
