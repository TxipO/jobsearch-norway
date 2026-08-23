"""Regression tests for rescore_all's cross-source duplicate detection.

Measured before building (2026-07-17): only 3 confirmed cross-source
duplicate pairs existed among ~2000 active vacancies (all NAV+finn.no),
zero NAV+Jobbnorge overlap. Small enough that a full merge/relink UI isn't
worth it — this locks in the lighter approach actually shipped: reuse the
existing excluded/exclusion_reason mechanism instead of a new one."""

import db
from scoring import _dedup_key, _exclude_cross_source_duplicates, rescore_all


def _make_conn(tmp_path):
    return db.connect(tmp_path / "test.db")


def _insert(conn, uuid, source, title, business_name, municipal, description="A description long enough to not look like a summary line."):
    db.upsert_vacancy_row(
        conn,
        {
            "uuid": uuid, "status": "ACTIVE", "title": title,
            "business_name": business_name, "municipal": municipal,
            "description": description,
        },
        source=source,
    )


def test_dedup_key_ignores_company_suffix_and_case():
    assert _dedup_key("Gullfunn AS", "Butikkleder", "Drammen") == \
        _dedup_key("gullfunn", "BUTIKKLEDER", "DRAMMEN")


def test_cross_source_duplicate_gets_excluded(tmp_path):
    conn = _make_conn(tmp_path)
    _insert(conn, "nav-1", "nav", "Butikkleder Gullfunn", "Gullfunn AS", "Drammen")
    _insert(conn, "finn-1", "finn", "Butikkleder Gullfunn", "Gullfunn AS", "Drammen")

    rescore_all(conn)

    nav_row = db.get_vacancy(conn, "nav-1")
    finn_row = db.get_vacancy(conn, "finn-1")
    excluded_flags = {nav_row["excluded"], finn_row["excluded"]}
    assert excluded_flags == {0, 1}, "exactly one of the pair should be flagged, not both and not neither"

    excluded_row = nav_row if nav_row["excluded"] else finn_row
    assert "Дублікат" in excluded_row["exclusion_reason"]


def test_same_employer_different_city_not_flagged_as_duplicate(tmp_path):
    """Live consideration: REMA 1000 posts near-identical 'Butikkmedarbeider'
    titles in many different towns — matching on employer+title alone would
    wrongly collapse them. municipal must be part of the key."""
    conn = _make_conn(tmp_path)
    _insert(conn, "nav-oslo", "nav", "Butikkmedarbeider", "REMA 1000", "Oslo")
    _insert(conn, "nav-bergen", "nav", "Butikkmedarbeider", "REMA 1000", "Bergen")

    rescore_all(conn)

    assert db.get_vacancy(conn, "nav-oslo")["excluded"] == 0
    assert db.get_vacancy(conn, "nav-bergen")["excluded"] == 0


def test_same_source_repeated_posting_not_flagged(tmp_path):
    """A single source (e.g. Jobbnorge) legitimately re-listing the same
    employer/title/city pair for two distinct openings shouldn't be treated
    as a cross-source duplicate — the whole point is catching the SAME
    posting appearing via two different pipelines, not two real vacancies
    that happen to look similar within one feed."""
    conn = _make_conn(tmp_path)
    _insert(conn, "jn-1", "jobbnorge", "Saksbehandler", "Oslo kommune", "Oslo")
    _insert(conn, "jn-2", "jobbnorge", "Saksbehandler", "Oslo kommune", "Oslo")

    rescore_all(conn)

    assert db.get_vacancy(conn, "jn-1")["excluded"] == 0
    assert db.get_vacancy(conn, "jn-2")["excluded"] == 0


def test_hard_block_propagates_to_unblocked_duplicate_copy(tmp_path):
    """Live bug 2026-08-10: a Politiets IT-enhet posting requiring
    sikkerhetsklarering was blocked on its NAV copy (long description, regex
    matched) but stayed visible on its finn.no copy, whose shorter/different
    scraped text didn't happen to contain the clearance phrase. Once ANY
    copy of a cross-source duplicate is hard-blocked, every copy describes
    the same real job and must be blocked too."""
    conn = _make_conn(tmp_path)
    _insert(conn, "nav-1", "nav", "Applikasjonstekniker", "Politiets IT-enhet", "Oslo",
            description="Du må kunne sikkerhetsklareres til HEMMELIG før tiltredelse.")
    _insert(conn, "finn-1", "finn", "Applikasjonstekniker", "Politiets IT-enhet", "Oslo",
            description="En kort tekst uten den setningen i det hele tatt her.")

    rescore_all(conn)

    nav_row = db.get_vacancy(conn, "nav-1")
    finn_row = db.get_vacancy(conn, "finn-1")
    assert nav_row["excluded"] == 1
    assert finn_row["excluded"] == 1
    assert "sikkerhetsklarering" in finn_row["exclusion_reason"].lower()


def test_dedup_tie_break_stable_regardless_of_candidate_order(tmp_path):
    """Live bug 2026-08-15: with score as the only sort key, a tie between
    equal-scoring duplicate copies fell back to Python's stable-sort
    tiebreak — the order the candidate list happened to be built in, which
    mirrors iter_scorable_vacancies()'s unordered SELECT and isn't
    guaranteed stable across separate rescore_all() runs. 4 real pairs
    flipped which twin was excluded between two back-to-back runs on the
    same data. Feeding the exact same two equal-score candidates in both
    orders (standing in for two differently-ordered SELECTs) must pick the
    same keeper either way — proof the tie-break no longer depends on
    input order."""
    conn = _make_conn(tmp_path)
    _insert(conn, "nav-1", "nav", "Driftsleder", "Politiet", "Oslo")
    _insert(conn, "jobbnorge-1", "jobbnorge", "Driftsleder", "Politiet", "Oslo")
    key = _dedup_key("Politiet", "Driftsleder", "Oslo")
    tied = [
        {"uuid": "nav-1", "score": 40, "source": "nav", "key": key},
        {"uuid": "jobbnorge-1", "score": 40, "source": "jobbnorge", "key": key},
    ]

    _exclude_cross_source_duplicates(conn, [dict(c) for c in tied])
    excluded_forward = db.get_vacancy(conn, "nav-1")["excluded"], db.get_vacancy(conn, "jobbnorge-1")["excluded"]

    db.set_exclusion(conn, "nav-1", False, None)
    db.set_exclusion(conn, "jobbnorge-1", False, None)

    _exclude_cross_source_duplicates(conn, [dict(c) for c in reversed(tied)])
    excluded_reversed = db.get_vacancy(conn, "nav-1")["excluded"], db.get_vacancy(conn, "jobbnorge-1")["excluded"]

    assert excluded_forward == excluded_reversed


def test_dedup_exclusion_stable_across_repeated_rescore_all_runs(tmp_path):
    conn = _make_conn(tmp_path)
    _insert(conn, "nav-1", "nav", "Driftsleder", "Politiet", "Oslo")
    _insert(conn, "jobbnorge-1", "jobbnorge", "Driftsleder", "Politiet", "Oslo")

    rescore_all(conn)
    first_run = db.get_vacancy(conn, "nav-1")["excluded"], db.get_vacancy(conn, "jobbnorge-1")["excluded"]

    rescore_all(conn)
    second_run = db.get_vacancy(conn, "nav-1")["excluded"], db.get_vacancy(conn, "jobbnorge-1")["excluded"]

    assert first_run == second_run


def test_non_new_user_status_survives_a_dedup_flip(tmp_path):
    """The real risk the fix above targets: user_status is stored per-uuid,
    not per dedup group, so if the twin the user marked 'applied' becomes
    the one dedup hides, the newly-visible twin must not silently read
    'new' — the tracked application would look lost, same failure shape as
    the 2026-07-29 data-loss incident that motivated db.backup_db()."""
    conn = _make_conn(tmp_path)
    _insert(conn, "nav-1", "nav", "Driftsleder", "Politiet", "Oslo")
    _insert(conn, "jobbnorge-1", "jobbnorge", "Driftsleder", "Politiet", "Oslo")

    rescore_all(conn)
    # Mark the currently-hidden duplicate as applied — the exact situation a
    # later dedup flip would otherwise orphan.
    hidden_uuid = "jobbnorge-1" if db.get_vacancy(conn, "jobbnorge-1")["excluded"] else "nav-1"
    db.set_user_status(conn, hidden_uuid, "applied")

    rescore_all(conn)

    nav_row = db.get_vacancy(conn, "nav-1")
    jn_row = db.get_vacancy(conn, "jobbnorge-1")
    visible = nav_row if not nav_row["excluded"] else jn_row
    assert visible["user_status"] == "applied"


def test_keeps_the_higher_scoring_duplicate(tmp_path):
    conn = _make_conn(tmp_path)
    # NAV description mentions remote work (real scoring signal, higher score);
    # finn digest has no description at all (matches its real limitation).
    _insert(conn, "nav-1", "nav", "IT-konsulent", "Acme AS", "Oslo",
            description="Fullt remote IT-support stilling, hjemmekontor mulig hele uken.")
    _insert(conn, "finn-1", "finn", "IT-konsulent", "Acme AS", "Oslo", description=None)

    rescore_all(conn)

    assert db.get_vacancy(conn, "nav-1")["excluded"] == 0
    assert db.get_vacancy(conn, "finn-1")["excluded"] == 1
