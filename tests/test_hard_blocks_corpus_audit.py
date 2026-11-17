"""Corpus audit 2026-11-17: every phrasing below is a real sentence/title from the live
corpus that used to slip through hard_blocks (positives), next to the hedged / optional /
alternative lookalike that must stay visible (negatives). See the "Corpus audit" comments in
hard_blocks.py for the reasoning behind each rule."""

import pytest

from hard_blocks import check_exclusion, iter_requirement_clauses

IT = "Systemutvikler"


def _blocked(body, title=IT):
    return check_exclusion(title, body)[0]


# --- titles -----------------------------------------------------------------------------

_BLOCKED_TITLES = [
    "Veterinær Bjerka", "AniCura Gjøvik søker dyrepleier", "Audiograf søkes til Fredrikstad Øre-Nese-Hals",
    "Psykiater Tynset modell 100% fast", "Sykehusapoteker Lillehammer - Vil du lede Sykehusapoteket?",
    "Fagradiograf CT med strålevernsfunksjon", "LIS 2/3 søkes til et faglig sterkt og engasjert miljø",
    "Overlegevikariat", "Avdelingsoverlege", "Legevikar sommeren 2027", "Legespesialist? Bli en del av legeteamet",
    "Ferievikar 2027 - Medisinstudent med lisens", "Anaesthetic Nurse Wanted – Anaesthesia Unit 1",
    "Radiologist position at Skien Hospital in Telemark", "Vi søker sakkyndig lungelege",
    "Helhetsblikk og klinisk ro? Randstad Care søker spesialist i indremedisin",
    "Pedagogiske ledere", "Støttepedagog", "Ressurspedagoger, fast stilling og vikariat",
    "Montessoripedagog - Jæren Montessoriskole", "Undervisningsstilling i norsk og samfunnsfag",
    "Rektor Tanabru skole", "Assisterende rektor", "Høgskulelektor i norsk",
    "Etterforskere PB 3/2/1", "Etterforsker PB3/2/1, avsnitt taktisk etterforskning", "Politi",
    "Grunnstilling politi - Geografisk driftseining Hordaland", "Politifaglig etterforskningsleder",
    "Pilot", "Vi søker Eiendomsmegler / Eiendomsmegler fullmektig",
]
_VISIBLE_TITLES = [
    "Dyreklinikksekretær (Resepsjonist) med ansvar for kundemottak ved VeteriNærDeg as",  # employer name
    "Pedagogisk medarbeider", "Pedagogiske ledere og fagarbeidere/assistenter",  # assistant role is open
    "UAS Pilot", "ROV Pilot/Technician & Senior ROV Pilot", "Prorektor for utdanning",
    "Etterforsker - Kriminalteknikk", "Politisk sekretær", "Menighetspedagog 100% fast stilling",
    "Seniorkonsulent radiologisk avdeling", "IKT-rådgiver radiologi", "Eiendomsmeglerkjede søker markedsrådgiver",
    "Konsulent - Eikeli videregående skole", "Helsesekretær",  # judged on the body, not the title
]


@pytest.mark.parametrize("title", _BLOCKED_TITLES)
def test_audit_authorised_and_regulated_titles_blocked(title):
    assert check_exclusion(title, "")[0], title


@pytest.mark.parametrize("title", _VISIBLE_TITLES)
def test_audit_title_lookalikes_not_blocked(title):
    assert not check_exclusion(title, "")[0], title


# --- security clearance ----------------------------------------------------------------

_CLEARANCE_REQUIRED = [
    "For å være aktuell kandidat til stillingen må du være kvalifisert for å bli sikkerhetsklarert, jfr. sikkerhetsloven.",
    "Søkere må tilfredsstille kravene til høyeste sikkerhetsklarering.",
    "Søkjarar må tilfredsstille krava til høgaste tryggingsklarering.",
    "Det forutsettes at den som ansettes må kunne klareres og autoriseres i tråd med sikkerhetslovens bestemmelser.",
    "Den som blir tilsett må kunna klarerast og autoriserast for hemmeleg jf. sikkerhetsloven",
    "Du må kunne autoriserast for avgrensa etter sikkerheitslova.",
    "Du må kunne autoriseres og klareres til hemmelig. NND er underlagt sikkerhetsloven.",
    "Kvalifikasjoner:\n- Kunne sikkerhetsklareres på nivå hemmelig\n- Førerkort klasse B",
    "Snakker flytende norsk og kan sikkerhetsklareres.",
    "For ansettelse i stillingen må sikkerhetsklarering påregnes.",
    "Det stilles krav til å kunne sikkerhetsklareres, da vi jobber med nasjonal kritisk infrastruktur.",
    "Den som ansettes må kunne autoriseres for behandling av informasjon gradert begrenset eller høyere etter sikkerhetsloven",
    "Stillinga krev at du må autoriserast for begrenset definert i sikkerhetslova.",
    "For denne stillingen betyr det at du må sikkerhetsklareres og autoriseres på nivå hemmelig / NATO Secret.",
    "Sikkerhetsklarering (nivå \"konfidensielt\") for tilsetting ved stasjonen",
    "Attention: candidates have to be eligible for a Norwegian or German security clearance!",
    "For this position, you must satisfy the security clearance requirements for Secret/NATO Secret.",
    "Current or recently held security clearance under the Norwegian Security Act at minimum Secret/NATO Secret.",
    # a hedge on the PREVIOUS line must not leak into the sentence that follows it
    "This employment may require a pre-employment check procedure.\nThis employment requires security clearance by the Norwegian National Security Authority.",
]
_CLEARANCE_HEDGED = [
    "Det er pr. i dag ikke et krav om sikkerhetsklarering for denne stillingen, men et slikt krav kan bli stilt på et senere tidspunkt.",
    "Sikkerhetsklarering kan bli aktuelt i forbindelse med ansettelse.",
    "Det kan være aktuelt med sikkerhetsklarering.",
    "For ansatte i selskapet kan det bli aktuelt med sikkerhetsklarering.",
    "Flytende norsk og mulighet for sikkerhetsklarering",
    "Du bør kunne sikkerhetsklareres, da flere av våre kunder krever dette",
    "Sikkerhetsklarering kan bli nødvendig dersom stillingens arbeidsoppgaver tilsier dette.",
    "Alle ansatte på en flyplass må sikkerhetsklareres før man kan tiltre stillingen.",  # airport background check
    "Mulighet for politiattest for sikkerhetsklarering ISPS.",
    "Candidates must pass a pre-employment background check and/or customer-mandated security clearances.",
    "This employment may require a background check, or security clearance by the Norwegian national security authority.",
    "Sikkerhetsklarering er ikke påkrevd.",
    "Les mer om sikkerhetsklarering på ffi.no.",
    "Tollklarering og dokumenthåndtering.",
    "Du må ha autorisasjon for tilgang til driftsmiljøet.",
    "Ønskede kvalifikasjoner:\n- Kan sikkerhetsklareres",
    "Nord universitet er underlagt sikkerhetsloven og dette stiller krav til håndtering av skjermingsverdig informasjon.",
]


@pytest.mark.parametrize("body", _CLEARANCE_REQUIRED)
def test_audit_clearance_requirement_blocked(body):
    assert _blocked(body), body


@pytest.mark.parametrize("body", _CLEARANCE_HEDGED)
def test_audit_clearance_hedged_not_blocked(body):
    assert not _blocked(body), body


# --- authorised health professions in the body -------------------------------------------

_AUTH_REQUIRED = [
    "Kvalifikasjoner:\n- Norsk autorisasjon som helsesekretær",
    "Kvalifikasjoner:\n- Autorisert sykepleier/vernepleier/helsefagarbeider",
    "Kvalifikasjoner:\n- Autorisert helsefagarbeider eller sykepleier",
    "Du har utdanning som sykepleier eller vernepleier med norsk autorisasjon.",
    "Kvalifikasjoner:\n- Sykepleier med autorisasjon.",
    "Kvalifikasjoner:\n- Sykepleier- eller vernepleierutdanning med autorisasjon",
    "Bachelor sykepleier eller vernepleier, med norsk autorisasjon. Vi ber deg opplyse ditt HPR-nummer.",
    "Vi søker en autorisert audiograf til 100% fast stilling.",
    "Kvalifikasjoner:\n- Norsk autorisasjon som sjukepleier med videreutdanning innan psykiatri.",
    "For stillingen kreves det norsk spesialistgodkjenning i fødselshjelp og kvinnesykdommer.",
    "Du er autorisert sykepleier, gjerne med erfaring fra sykehjem.",
    "Kvalifikasjoner:\n- Gyldig norsk autorisasjon som helsesekretær, oppgi HPR nr i søknaden",
    "Vi søker autoriserte helsesekretærer til vikariater ved legekontor.",
]
_AUTH_OPTIONAL = [
    "Autorisasjon som helsesekretær er ønskelig, men søkere med relevant erfaring kan søke.",
    "Norsk autorisasjon som helsesekretær eller annen relevant helsefaglig bakgrunn.",
    "Norsk autorisasjon som helsesekretær eller relevant kontorfaglig utdanning.",
    "Fortrinnsvis norsk autorisasjon som helsesekretær",
    "Vi søker primært deg som er autorisert helsesekretær. Andre kan også vurderes.",
    "Autorisert helsesekretær, medisinsk sekretær, sekretær",
    "Blodprøvetaking for autoriserte helsesekretærer",
    "Autorisert helsesekretær foretrekkes, annen relevant utdanning kan vurderes.",
    "Ønskede kvalifikasjoner:\n- Autorisasjon som helsesekretær",
    "Norsk autorisasjon som apotektekniker, eventuelt annen arbeidserfaring innen apotek.",
    "Har du kontorfaglig bakgrunn eller er du autorisert helsepersonell? Søk deg til vår seksjon!",
    "For stillinger som sykepleier, vernepleier og helsefagarbeider kreves norsk autorisasjon.",
    "Autorisert helsefagarbeider eller assistent med erfaring fra hjemmetjenesten",
    "Autorisert sykepleier eller IKT-utdanning",
]


@pytest.mark.parametrize("body", _AUTH_REQUIRED)
def test_audit_authorised_profession_in_body_blocked(body):
    assert _blocked(body, "Tilkallingsvikar"), body


@pytest.mark.parametrize("body", _AUTH_OPTIONAL)
def test_audit_authorised_profession_optional_not_blocked(body):
    assert not _blocked(body, "Tilkallingsvikar"), body


# --- other regulated licences, foreign language, citizenship -----------------------------

def test_audit_regulated_licences_in_body_blocked_and_soft_forms_not():
    for body in [
        "Vi søker en statsautorisert regnskapsfører med solid erfaring.",
        "Stillingen lønnes som politibetjent 3/2/1 (stillingskode 1461/1459/1457) med årslønn:",
        "Krav:\n- Bestått vitnemål fra politihøgskole/politiskole",
        "Krav:\n- Gyldig dekksoffisersertifikat D5 eller høyere",
        "Du har bestått installatørprøven og godkjenning fra DSB som elektroinstallatør.",
        "Du må ha godkjent pedagogisk utdanning for å bli tilsett i lærerstilling.",
        "Kvalifikasjoner:\n- Krav om undervisningskompetanse i mer enn ett fag",
        "Stillingen krever norsk advokatbevilling.",
    ]:
        assert _blocked(body, "Rådgiver"), body
    for body in [
        "Er du statsautorisert rekneskapsførar, er det eit pluss, men ikkje eit krav. Vi søkjer ein statsautorisert rekneskapsførar.",
        "Ønskede kvalifikasjoner\nAutorisasjon som regnskapsfører",
        "Det er en fordel om du også har:\n- En godkjenning som statsautorisert revisor",
        "Alternativt stilling som politibetjent 3/2/1, jf. kompetansekriteriene for politibetjenter.",
        "Dekksoffisersertifikat er en fordel.",
        "Godkjent lærerutdanning er ønskelig.",
        "Vi søker deg med godkjent lærerutdanning eller annen undervisningskompetanse.",
        "Gyldig DSB-godkjenning for stillinger der dette kreves.",
    ]:
        assert not _blocked(body, "Rådgiver"), body
    # an ad that also hires assistants: the teacher sentence is conditional
    assert not _blocked("Du må ha godkjent pedagogisk utdanning for å bli tilsett i lærerstilling.",
                        "Tilkallingsvikarer fagarbeidere/assistenter")


def test_audit_foreign_language_requirement_in_body():
    for body in [
        "Language: German and English\n* Only apply if you speak German fluently.",
        "- Fluent in German and English.",
        "Meget gode kunnskaper i svensk og engelsk, både muntlig og skriftlig",
        "Du må snakke polsk og engelsk og aller helst også norsk.",
        "Har svært gode tyskkunnskaper, både muntlig og skriftlig.",
    ]:
        assert _blocked(body, "Kontormedarbeider"), body
    for body in [
        "Må kunne kommunisere på norsk, svensk eller dansk.",
        "Fluency in at least one of the following languages: Swedish, Norwegian, Danish, German or Dutch.",
        "Behersker norsk eller engelsk. Vi vurderer også søkere som snakker polsk.",
        "Tysk er en fordel, men ikke et krav.",
        "Du må beherske norsk og engelsk godt. Det er en fordel om du forstår svensk godt.",
        "Fluent in English. German or Dutch is a plus.",
        "Vi har et svensk hovedkontor i Skänninge.",
    ]:
        assert not _blocked(body, "Kontormedarbeider"), body


def test_audit_optional_heading_wins_over_requirement_heading():
    """REQUIREMENT_HEADING_RE's optional prefix word swallowed "Ønskede kvalifikasjoner" as a
    requirements heading, so every bullet under it read as required."""
    clauses = dict(iter_requirement_clauses("ønskede kvalifikasjoner\nerfaring med azure\n"
                                            "kvalifikasjoner\nerfaring med linux"))
    assert clauses["erfaring med azure"] is False
    assert clauses["erfaring med linux"] is True
    flags = dict(iter_requirement_clauses("det er en fordel om du også har:\nfagbrev"))
    assert flags["fagbrev"] is False


def test_audit_dsb_authorisation_blocked_as_licence_not_health():
    assert _blocked("Arbeidet krever autorisasjon fra DSB for elvirksomhet", "IT-driftstekniker")
