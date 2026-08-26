"""Regression tests for hard_blocks.py — each block category, plus the two
live false positives that shaped the current body-level regex (Otium's
conditional authorisation clause, and the far+low-extent block added
2026-07-17)."""

from hard_blocks import check_exclusion


def test_health_authorisation_title_blocked():
    excluded, reason = check_exclusion("Sykepleier", "Vi søker etter deg.")
    assert excluded
    assert "авторизація" in reason


def test_teaching_title_blocked():
    excluded, _ = check_exclusion("Lærer i matematikk", "")
    assert excluded


def test_academic_title_blocked():
    excluded, _ = check_exclusion("Stipendiat i sosiologi", "")
    assert excluded


def test_fagbrev_trade_title_blocked():
    excluded, _ = check_exclusion("Elektriker søkes", "")
    assert excluded


def test_licence_title_blocked():
    excluded, _ = check_exclusion("Bussjåfør", "")
    assert excluded


def test_laerling_title_blocked():
    """Fixed 2026-07-17: lærling used to grant a scoring BONUS; a Norwegian
    apprenticeship actually requires completed Vg1+Vg2 first, so it's a
    hard block now, same as fagbrev."""
    excluded, reason = check_exclusion("Lærling i service- og administrasjonsfaget", "")
    assert excluded
    assert "Vg" in reason

    excluded2, _ = check_exclusion("Ledige læreplassar i barne- og ungdomsarbeidarfaget", "")
    assert excluded2


def test_ordinary_it_support_title_not_blocked():
    excluded, reason = check_exclusion(
        "IT-support medarbeider", "Vi søker en engasjert person til vårt serviceteam."
    )
    assert not excluded
    assert reason is None


def test_conditional_authorisation_clause_not_blocked():
    """Live false positive (Otium bo- og velferdssenter tilkallingsvikar):
    'norsk autorisasjon kreves for søkere som er sykepleiere...' only
    requires it IF you happen to be a nurse, on a posting also open to
    assistants ('søker etter assistenter og helsepersonell')."""
    excluded, _ = check_exclusion(
        "Tilkallingsvikar",
        "Vi søker etter assistenter og helsepersonell. God opplæring gis. "
        "Norsk autorisasjon kreves for søkere som er sykepleiere, vernepleiere, "
        "eller helsefagarbeidere.",
    )
    assert not excluded


def test_direct_authorisation_requirement_blocked():
    excluded, _ = check_exclusion(
        "Nattevakt i hjemmet",
        "Du har 3-årig sykepleieutdanning, og har norsk autorisasjon som sykepleier.",
    )
    assert excluded


def test_doctor_compound_titles_blocked():
    """Live example, 2026-07-19 (user-flagged): 'Kommuneoverlege og
    fastlege' scored 16% and wasn't blocked — \\blege\\b/\\boverlege\\b
    don't match inside compounds (no internal word boundary)."""
    for title in ["Kommuneoverlege og fastlege", "Tilsynslege 70%", "Assisterende fylkeslege"]:
        excluded, reason = check_exclusion(title, "")
        assert excluded, f"{title!r} should be blocked"
        assert "медпрацівника" in reason


def test_doctor_compound_false_positives_not_blocked():
    """Live false-positive risk found auditing real matches: 'lege' as a
    substring inside unrelated words — a workplace name (legesenter,
    legeutdanning), an employer name (Leger Uten Grenser), or pure
    coincidence (samfunnsvitskaplege, Nynorsk for 'social-science',
    nothing to do with medicine)."""
    assert not check_exclusion("Helsesekretær", "70% stilling ved Frekhaug legesenter.")[0]
    assert not check_exclusion("Rådgiver ved Enhet for legeutdanning", "")[0]
    assert not check_exclusion("Giverassistent hos Leger Uten Grenser", "")[0]


def test_police_officer_ranks_blocked():
    """Live example, 2026-07-19 (user-flagged): 'Politibetjent 3/2/1'
    scored 10% and wasn't blocked at all — no police category existed."""
    for title in ["Politibetjent 3/2/1", "Politioverbetjent", "Politiførstebetjent - Vaktleder"]:
        excluded, reason = check_exclusion(title, "")
        assert excluded, f"{title!r} should be blocked"
        assert "Politihøgskolen" in reason


def test_police_policy_word_not_blocked():
    """'politikk' (policy) contains 'politi' as a substring but has
    nothing to do with the police — a bare 'politi' pattern would have
    false-matched this."""
    excluded, _ = check_exclusion("Vil du videreutvikle Norges KI-politikk?", "")
    assert not excluded


def test_civilian_police_org_role_not_blocked():
    """A civilian/administrative role at politiet (not a sworn officer
    rank) must not be blocked by title alone — too ambiguous, Norway does
    have civilian roles inside politiet."""
    excluded, _ = check_exclusion("Vil du bidra i politiets digitaliseringsreise?", "")
    assert not excluded


def test_maskinsjef_blocked():
    """Live example, 2026-07-19 (user-flagged): 'Ambulerende Maskinsjef
    på MF Petter Dass' requires a Sjøfartsdirektoratet marine engineer
    certificate — same category as styrmann/skipsfører already blocked."""
    excluded, reason = check_exclusion("Ambulerende Maskinsjef på MF Petter Dass", "")
    assert excluded
    assert "сертифікат" in reason


def test_juridisk_radgiver_blocked():
    """Live example, 2026-07-19 (user-flagged): 'Juridisk rådgiver' scored
    21% and wasn't blocked — 'jurist' pattern doesn't match the adjective
    'juridisk'. Description explicitly requires 'master i rettsvitenskap/
    cand.jur'."""
    excluded, reason = check_exclusion(
        "Juridisk rådgiver",
        "Dette må du ha for å være kvalifisert: master i rettsvitenskap/cand.jur",
    )
    assert excluded
    assert "право" in reason


def test_radgiver_juridisk_vikariat_still_blocked():
    """Reversed word order ('Rådgiver juridisk (vikariat)') is a real
    variant seen on Jobbnorge — must still be caught after the
    department-noun negative lookahead was added."""
    excluded, _ = check_exclusion("Rådgiver juridisk (vikariat)", "")
    assert excluded


def test_radgiver_juridisk_department_not_blocked():
    """code-review 2026-07-19: 'rådgiver juridisk' without the lookahead
    also matched a generalist advisor merely attached to a legal
    department ('Rådgiver juridisk seksjon'/'avdeling') — that role
    doesn't require a law degree, unlike 'Juridisk rådgiver' itself."""
    assert not check_exclusion("Rådgiver juridisk seksjon", "")[0]
    assert not check_exclusion("IT-rådgiver juridisk avdeling", "")[0]


def test_juridisk_faculty_and_subject_not_blocked():
    """Live false-positive risk: bare 'juridisk' would also match a
    workplace name ('Det juridiske fakultet') and a subject-matter
    descriptor on an unrelated role ('juridiske fag' on a librarian
    posting) — neither requires the role-holder to have a law degree."""
    assert not check_exclusion(
        "Vikariat som seniorkonsulent, Det juridiske fakultet", ""
    )[0]
    assert not check_exclusion("Universitetsbibliotekar - juridiske fag", "")[0]


def test_security_clearance_requirement_blocked():
    """Live example, 2026-07-18: Forsvaret cyber-defense posting requiring
    Norwegian security clearance — unreachable without citizenship."""
    excluded, reason = check_exclusion(
        "Cyberoperatør",
        "Du må kunne sikkerhetsklareres til HEMMELIG og NATO SECRET før tiltredelse.",
    )
    assert excluded
    assert "sikkerhetsklarering" in reason


def test_security_clearance_generic_disclaimer_not_blocked():
    """Live false-positive risk found auditing 164 real matches: a
    fylkeskommune boilerplate disclaimer ('enkelte stillinger vil kunne
    kreve sikkerhetsklarering' — SOME positions in the organization, not
    necessarily this one) appeared on completely unrelated postings
    (Tannpleier, Arealplanlegger, Prosjektledere) that have nothing to do
    with clearance. Uses "Arealplanlegger" here, not the real "Tannpleier"
    example — that title is independently health-authorisation-blocked,
    which would pass this test for the wrong reason."""
    excluded, _ = check_exclusion(
        "Arealplanlegger",
        "Det betyr at enkelte stillinger vil kunne kreve sikkerhetsklarering "
        "før eventuell tilsetting. Vi er en av landsdelens største arbeidsgivere.",
    )
    assert not excluded


def test_far_and_low_extent_blocked():
    """Added 2026-07-17: relocating outside Vestland for under 60% doesn't
    cover rent. Only fires when extent_percent is actually known."""
    excluded, reason = check_exclusion(
        "Butikkmedarbeider", "", county="Oslo", extent_percent=30
    )
    assert excluded
    assert "переїзд" in reason


def test_far_but_full_time_not_blocked():
    excluded, _ = check_exclusion(
        "Butikkmedarbeider", "", county="Oslo", extent_percent=100
    )
    assert not excluded


def test_far_but_unknown_extent_not_blocked():
    """Per the user's own steer ('не вгадаєш') — an unresolved percentage is
    shown, not hidden, even far from Vestland."""
    excluded, _ = check_exclusion(
        "Butikkmedarbeider", "", county="Oslo", extent_percent=None
    )
    assert not excluded


def test_low_extent_inside_vestland_not_blocked():
    excluded, _ = check_exclusion(
        "Butikkmedarbeider", "", county="Vestland", extent_percent=20
    )
    assert not excluded


def test_autorisasjon_etter_sikkerhetsloven_is_blocked():
    """Live gap found 2026-08-15: Brønnøysundregistrene's "Vi søker
    systemutviklere" (jobbnorge-305037) scored 46 and sat unblocked. The
    clearance category has existed since 2026-07-18, but its regex only knew
    the "sikkerhetsklarering" wording, and this ad uses the authorisation
    wording for the same law."""
    excluded, reason = check_exclusion(
        "Vi søker systemutviklere",
        "Du må kunne autoriseres for BEGRENSET etter sikkerhetsloven. Er du "
        "utenlandsk statsborger, skal autorisasjonsansvarlig hos oss vurdere om "
        "din tilknytning til hjemlandet utgjør en risiko knyttet til stillingen.",
    )
    assert excluded
    assert "sikkerhetsklarering" in reason


def test_enkelte_stillinger_boilerplate_still_not_blocked_for_autorisasjon():
    """The 2026-07-18 false-positive guard has to cover the wording added on
    2026-08-15 too: an employer-wide disclaimer that SOME posts may need
    authorisation is not a requirement of this particular job."""
    # Title is deliberately "Arealplanlegger" and not the "Tannpleier" named
    # in the 2026-07-18 audit note: Tannpleier is separately blocked as a
    # regulated health profession, so it can never prove anything about the
    # clearance guard.
    excluded, _ = check_exclusion(
        "Arealplanlegger",
        "Vi minner om at enkelte stillinger hos oss må kunne autoriseres for "
        "BEGRENSET etter sikkerhetsloven.",
    )
    assert not excluded


def test_eu_passport_requirement_blocked():
    """Live case 2026-08-15, user-flagged: "Norwegian speaker? Kick-start
    your international career in Greece!" (Jobs By Nordics AB) — a BPO
    recruiting ad for a job physically in Greece, not Norway, that slipped
    into the NAV feed and scored 47%."""
    excluded, reason = check_exclusion(
        "Norwegian speaker? Kick-start your international career in Greece!",
        "You hold a valid EU passport. Free flight to Greece, work from Athens.",
    )
    assert excluded
    assert "EU passport" in reason


def test_relocation_to_norway_not_blocked_by_eu_passport_check():
    """The check must key off the specific "EU passport" phrase, not any
    mention of relocation/moving — legitimate Norway-based employers do
    offer relocation packages to candidates moving TO Norway (e.g. Dignus
    Medical's psychiatrist postings, live corpus 2026-08-15)."""
    excluded, _ = check_exclusion(
        "Specialist Psychiatrist – Western Norway",
        "We offer a generous relocation package and support with finding "
        "housing for candidates moving to Norway from abroad.",
    )
    assert not excluded


def test_truckforerbevis_hard_requirement_blocked():
    """Live case, real corpus 2026-08-26: "Vi trenger lagermedarbeidere med
    truckførerbevis" — body says "truckførerbevis t1-t4 er et krav", an
    unambiguous hard requirement with no training offered."""
    excluded, reason = check_exclusion(
        "Vi trenger lagermedarbeidere med truckførerbevis",
        "Erfaring fra lager eller truckkjøring. Truckførerbevis t1-t4 er et krav. Du må kunne kommunisere på norsk.",
    )
    assert excluded
    assert "truckførerbevis" in reason.lower()


def test_truckforerbevis_soft_mention_not_blocked():
    """The dominant real phrasing — "er en fordel, men ikke et krav" — must
    NOT block. Measured live: ~92/112 truckfør*-vacancies use this or
    similar soft/optional phrasing."""
    excluded, _ = check_exclusion(
        "Produksjonsmedarbeider",
        "Truckførerbevis er en fordel, men ikke et krav. God fysisk form er ønskelig.",
    )
    assert not excluded


def test_truckforerbevis_role_title_blocked():
    """A "Truckfører" role title structurally needs the certificate to do
    the job at all, regardless of body phrasing."""
    excluded, reason = check_exclusion("Truckfører Vestby", "Bli med i et godt arbeidsmiljø.")
    assert excluded
    assert "truckførerbevis" in reason.lower()


def test_truckforerbevis_training_offered_overrides_hard_requirement():
    """User-requested override 2026-08-26: "якщо вони вказують, що будуть
    навчати на місці прям - тоді проходить" — training offered must save
    even an otherwise-hard requirement from being blocked."""
    excluded, _ = check_exclusion(
        "Lagermedarbeider",
        "Truckførerbevis er et krav, men opplæring vil bli gitt til rett kandidat.",
    )
    assert not excluded
