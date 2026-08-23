"""Regression tests for scoring.py — mostly false-positive traps found on
live data this session. Each test here corresponds to a real bug caught by
inspecting score_breakdown on an actual vacancy, not a hypothetical."""

from scoring import _parse_salary, _salary_min_value, score_vacancy


def _score(title, description, municipal=None, county=None, language="no", occupation_categories=None):
    return score_vacancy(title, description, municipal, county, language, occupation_categories)


def test_junior_in_body_does_not_grant_entry_level_bonus():
    """Live bug: 'senior advisor and support junior engineers' described a
    SENIOR position, but bare-word matching on 'junior' anywhere in the
    body gave it the entry-level bonus anyway."""
    _, bd = _score(
        "Senior Subsea Operations Engineer",
        "Act as senior advisor and support junior engineers in layout and operations topics.",
    )
    assert bd["entry_level_bonus"]["points"] == 0
    assert bd["senior_penalty"]["points"] < 0


def test_delivery_in_body_does_not_grant_general_entry_bonus():
    """Live bug: 'project delivery' matched a bare 'delivery' keyword meant
    to catch parcel/food delivery jobs."""
    _, bd = _score(
        "Project Engineer",
        "Responsible for project delivery, verification, and scheduling.",
    )
    assert "delivery" not in bd["track_general_entry_level"]["matched"]


def test_not_remote_does_not_grant_remote_bonus():
    """Live bug: 'On-site only (not remote)' contains the substring
    'remote', which a naive `in text` check matched as a positive signal."""
    _, bd = _score(
        "Alarm Operator",
        "This is an on-site position, not remote — you work from our office.",
    )
    assert bd["remote_bonus"]["points"] == 0


def test_actual_remote_still_scores():
    _, bd = _score("IT Support", "Fully remote position, hjemmekontor available.")
    assert bd["remote_bonus"]["points"] > 0


def test_hjemmekontor_perk_mention_does_not_grant_remote_bonus():
    """Live bug 2026-08-15, user-flagged: Rogaland fylkeskommune's
    "IT-rådgiver i seksjon IT og brukerstøtte" is an on-site Stavanger role
    that scored 55%, +15 of it from "mulighet for hjemmekontor etter
    gjeldende retningslinjer" — a generic benefits-list perk mention, not a
    remote-work arrangement. Same failure shape as the hybrid/remote fix
    above; measured against the live corpus, 218/220 bare "hjemmekontor"
    matches were this exact perk-list pattern."""
    _, bd = _score(
        "IT-rådgiver",
        "Vi tilbyr fleksibel arbeidstid og mulighet for hjemmekontor etter gjeldende retningslinjer.",
    )
    assert bd["remote_bonus"]["points"] == 0


def test_hjemmekontor_as_primary_arrangement_still_scores():
    _, bd = _score("Kundekonsulent", "Stillingen er 100 % hjemmekontor, ingen oppmøte på kontor.")
    assert bd["remote_bonus"]["points"] > 0


def test_hybrid_industry_compound_does_not_grant_remote_bonus():
    """Live bug 2026-08-01 (flagged-vacancy queue): ABB "Project Engineer
    Automation" (on-site, Hammerfest) scored +15 because bare 'hybrid'
    substring-matched inside 'hybridindustri' ("hybrid industry" — a
    process-engineering term, unrelated to work-from-home)."""
    _, bd = _score(
        "Project Engineer Automation",
        "Vi leverer prosjekter innen prosess- og hybridindustri i Hammerfest.",
    )
    assert bd["remote_bonus"]["points"] == 0


def test_geographically_remote_location_does_not_grant_remote_bonus():
    """Live bug 2026-08-01 (flagged-vacancy queue): two Ytri Island Retreat
    chef postings scored +15 off "comfortable... working in a remote yet
    spectacular natural environment" — describing the island's isolation,
    not a remote-work arrangement (this is an on-site kitchen job)."""
    _, bd = _score(
        "Chef de Partie",
        "You take pride in your craft, and are comfortable living and working in a "
        "remote yet spectacular natural environment.",
    )
    assert bd["remote_bonus"]["points"] == 0


def test_hybrid_near_work_context_still_scores():
    """The real signal survives: 'hybrid' next to an actual work-modality
    word ('arbeidshverdag') — the exact boilerplate phrase copy-pasted
    across dozens of Skatteetaten postings."""
    _, bd = _score(
        "Utvikler",
        "Vi har en hybrid og fleksibel arbeidshverdag med mulighet for hjemmekontor noen dager.",
    )
    assert bd["remote_bonus"]["points"] > 0


def test_hybrid_title_shorthand_still_scores():
    """Recruiter title shorthand "(Oslo/Hybrid)" is trusted unconditionally
    — no work-context word needed nearby, since titles are short/curated."""
    _, bd = _score("Java/Kotlin-utviklere (Oslo/Hybrid)", "Bli med i teamet vårt.")
    assert bd["remote_bonus"]["points"] > 0


def test_english_phrased_norwegian_requirement_is_caught():
    """Live bug: the Norwegian-fluency check only matched Norwegian-phrased
    requirements ('flytende norsk'). An English ad describing the same
    requirement in English slipped through and got the English-language
    bonus on top, compounding the error."""
    _, bd = _score(
        "Alarm Operator",
        "You must have very good written and oral communication in Norwegian.",
        language="en",
    )
    assert bd["norwegian_fluency_penalty"]["points"] < 0


def test_bare_lager_does_not_match_the_verb_a_lage():
    """Live false-positive found 2026-07-18: bare 'lager' (meant to catch
    'warehouse') also matches the Norwegian verb 'å lage' ('to make')
    conjugated as 'lager' — real ad copy like 'produktene vi lager skal gi
    verdi' has nothing to do with warehouse work. 'lager' was removed from
    GENERAL_ENTRY_KEYWORDS entirely (56 DB matches audited, majority noise;
    genuine warehouse postings still match 'lagermedarbeider' or
    'logistikk')."""
    _, bd = _score(
        "Produktleder for skyplattform",
        "Vi jobber tett med kundene. Produktene vi lager skal gi verdi for innbyggerne.",
    )
    assert "lager" not in bd["track_general_entry_level"]["matched"]
    assert bd["track_general_entry_level"]["points"] == 0


def test_lagermedarbeider_still_matches():
    _, bd = _score("Lagermedarbeider", "Vi søker en lagermedarbeider til vårt sentrallager.")
    assert "lagermedarbeider" in bd["track_general_entry_level"]["matched"]


def test_production_and_warehouse_keywords_match():
    """Retargeted 2026-08-18 — user rejected retail, wants production/
    warehouse instead. New keywords must actually fire."""
    for title, description in [
        ("Produksjonsmedarbeider", "Vi søker produksjonsmedarbeider til vår fabrikk."),
        ("Truckfører", "Du har truckførerbevis og trives med fysisk arbeid på lager."),
        ("Prosessoperatør", "Som prosessoperatør styrer du produksjonslinjen."),
    ]:
        _, bd = _score(title, description)
        assert bd["track_general_entry_level"]["points"] > 0, title


def test_retail_keywords_removed_from_general_entry_track():
    """User explicitly rejected retail/shop-assistant roles (2026-08-18,
    "вакансії продавця теж нахуй, я не буду") — butikkmedarbeider/selger/
    resepsjon must no longer score a general-entry bonus."""
    _, bd = _score(
        "Butikkmedarbeider",
        "Vi søker en selger til vår resepsjon, retail-erfaring er en fordel.",
    )
    assert bd["track_general_entry_level"]["points"] == 0


def test_matproduksjon_and_varemottak_deliberately_not_keywords():
    """Both were tried and dropped — live corpus audit found them
    saturated with kitchen/restaurant/café ads ("Kokk", "kjøkkensjef",
    "tilkallingsvikar på kjøkkenet"), the exact category just rejected."""
    _, bd = _score(
        "Kokk – nytt restaurantkonsept",
        "Typiske arbeidsoppgaver: prep og matproduksjon, varemottak, tilberedning av retter.",
    )
    assert bd["track_general_entry_level"]["points"] == 0


def test_senior_in_title_is_penalized():
    _, bd = _score("Senior Backend Developer", "We need an experienced engineer.")
    assert bd["senior_penalty"]["points"] < 0


def test_fagbrev_requirement_penalized():
    score, bd = _score("Elektriker", "Søker etter fagarbeider, fagbrev kreves.")
    assert bd["degree_penalty"]["points"] < 0


def test_kvalifisert_checklist_degree_requirement_penalized():
    """Live gap found 2026-07-19 (user-flagged 'Juridisk rådgiver', 21%):
    the common Jobbnorge/public-sector checklist header 'Dette må du ha
    for å være kvalifisert: master i rettsvitenskap' wasn't caught by any
    existing DEGREE_REQUIRED_PATTERNS phrasing."""
    _, bd = _score(
        "Rådgiver", "Dette må du ha for å være kvalifisert:   master i rettsvitenskap/cand.jur",
    )
    assert bd["degree_penalty"]["points"] < 0


def test_salary_parses_currency_prefixed_figure():
    assert _parse_salary("Lønn kr 680 000 per år.") == "kr 680 000"


def test_salary_parses_range():
    assert _parse_salary("Lønn kr. 522.600-635.600 avhengig av erfaring.") == "kr. 522.600-635.600"


def test_salary_ignores_bare_numbers_without_currency_prefix():
    """Live false-positive risk found while researching this feature: a
    loose 6-digit regex matched org numbers, phone numbers, and dates in
    1164/1983 real descriptions. Only a number with 'kr'/'kr.' directly in
    front is trustworthy."""
    assert _parse_salary("Org.nr 364725, telefon 900 12 345.") is None
    assert _parse_salary("Søknadsfrist 15.09.2026, ref. 507400.") is None


def test_salary_none_when_absent():
    assert _parse_salary("Lønn etter avtale.") is None
    assert _parse_salary(None) is None


def test_salary_min_value_strips_thousand_separators():
    """Real DB samples use space, dot, and non-breaking-space as thousand
    separators inconsistently — all three must parse to the same int."""
    assert _salary_min_value("kr 680 000") == 680000
    assert _salary_min_value("kr. 601.000") == 601000
    assert _salary_min_value("Kr 50\xa0000") == 50000


def test_salary_min_value_takes_the_first_figure_in_a_range():
    assert _salary_min_value("kr. 522.600-635.600") == 522600


def test_salary_min_value_none_when_absent():
    assert _salary_min_value(None) is None


def test_people_management_requirement_is_penalized():
    """Live gap found 2026-08-15 (user pasted the vacancy expecting a søknad):
    Skatteetaten's "Er du en trygg leder som brenner for å levere god kvalitet
    og service?" (uuid 601adabd) scored 63 and sat near the top of the
    shortlist. It is an underdirektør post with personalansvar for 16 people,
    but it matched +32 of pure support keywords and took no penalty: the title
    says "leder" while SENIOR_TITLE_KEYWORDS only has "avdelingsleder", and the
    ad says "lang operativ ledererfaring" while YEARS_EXPERIENCE_PATTERNS wants
    a digit."""
    _, bd = _score(
        "Er du en trygg leder som brenner for å levere god kvalitet og service?",
        "Gruppen IT-support håndterer 1. og 2.linje brukerhenvendelser. "
        "personalansvar og utvikling av medarbeidere. "
        "lang operativ ledererfaring innen brukerstøtte med resultatansvar.",
    )
    assert bd["management_penalty"]["points"] < 0
    assert "personalansvar" in bd["management_penalty"]["matched"]


def test_assistant_coordination_role_is_not_penalized_as_management():
    """The other half of the same fix: adding "leder"/"manager" to the title
    keyword list would also have hit OsloMet's "Vikariat som assisterende
    Service Desk Manager", a coordination role with no reports that was a
    legitimate application. Only a body-level demand for staff responsibility
    counts as management."""
    _, bd = _score(
        "Vikariat som assisterende Service Desk Manager",
        "Du vil koordinere førstelinjen som består av studentassistenter, "
        "bidra til opplæring og videreutvikling av rutiner og veiledninger.",
    )
    assert bd["management_penalty"]["points"] == 0


def test_supportspesialist_title_matches_support_track():
    """Live gap found 2026-08-16: Tieto Banktech's "Customer Support
    Specialist" (Norwegian title: supportspesialist) scored 0/40 on the
    support track despite being a pure support role — none of the existing
    keywords matched the ad's own vocabulary (supportspesialist,
    supportteam, feilsøke, supportsaker)."""
    _, bd = _score(
        "Customer Support Specialist",
        "Som en av våre nye supportspesialister blir du en del av vårt supportteam.",
    )
    assert "supportspesialist" in bd["track_it_support"]["matched"]


def test_bare_kundestotte_and_feilsoke_deliberately_not_keywords():
    """The other half of the 2026-08-16 fix: bare "kundestøtte" and
    "feilsøke" were tried and reverted because they matched a bike
    mechanic, a Fast Food Service technician, and a telemarketer. Only the
    narrow job-title compounds were kept."""
    _, bd = _score(
        "Telefonselger",
        "Vi tilbyr god kundestøtte og hjelper deg å feilsøke salgsprosessen.",
    )
    assert bd["track_it_support"]["points"] == 0


def test_occupation_category_bonus_for_production_and_warehouse():
    """Added 2026-08-18 — NAV's own occupation_categories classification is
    more reliable than any keyword list (measured: keyword track alone
    missed 77% of titles under these two categories)."""
    import json
    cats = json.dumps([{"level1": "Industri og produksjon", "level2": "Matproduksjon"}])
    _, bd = _score("Ein liten jobb med stor betydning", "Vi søker deg.", occupation_categories=cats)
    assert bd["occupation_category_bonus"]["points"] > 0
    assert "Industri og produksjon" in bd["occupation_category_bonus"]["matched"]


def test_occupation_category_penalty_for_retail_and_hospitality():
    """User explicitly rejected these categories 2026-08-18."""
    import json
    cats = json.dumps([{"level1": "Salg og service", "level2": "Butikk"}])
    _, bd = _score("Butikkmedarbeider", "Vi søker en engasjert medarbeider.", occupation_categories=cats)
    assert bd["occupation_category_bonus"]["points"] < 0


def test_occupation_category_missing_or_malformed_is_neutral():
    """finn.no/LinkedIn rows never have this field — must not crash or
    silently penalize/bonus based on absent data."""
    _, bd = _score("Some title", "Some description.", occupation_categories=None)
    assert bd["occupation_category_bonus"]["points"] == 0
    _, bd = _score("Some title", "Some description.", occupation_categories="not json")
    assert bd["occupation_category_bonus"]["points"] == 0


def test_phone_support_channel_is_penalized():
    """User wants chat/written support, not phone-based (2026-08-18)."""
    _, bd = _score("Kundekonsulent", "Du jobber i vårt kundesenter med utgående samtaler.")
    assert bd["phone_support_penalty"]["points"] < 0


def test_written_support_not_penalized():
    _, bd = _score("Support Agent", "Du besvarer henvendelser via chat og e-post.")
    assert bd["phone_support_penalty"]["points"] == 0
