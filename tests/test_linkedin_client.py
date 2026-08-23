"""Regression test built from two real LinkedIn job-alert digest emails
(auto-forwarded via the Gmail filter, jobalerts-noreply@linkedin.com ->
the address gmail_client.py reads, 2026-08-09/08-10). The fixture below is a trimmed
excerpt of the real plain-text MIME body — tracking query params
shortened, but the dash-separated block structure (title / employer /
location / optional badge line(s) / "View job: {url}") is untouched,
since that structure is exactly what parse_digest() depends on."""

from linkedin_client import _strip_employer_suffix, parse_digest, to_vacancy_row

# Three real cases in one fixture: a plain job with no badge line
# (Self-Help/Customer Support), a job with a badge line between location
# and "View job:" ("This company is actively hiring"), and a location with
# no county segment ("Storebrand · Oslo, Norway" — only 2 comma-parts).
# The leading intro text (before the first "---" rule) mimics the real
# email's own preamble, which must NOT be mistaken for a job title.
REAL_DIGEST_EXCERPT = """\
Alex, your job alert for Help Desk Technician in Norway found new jobs

Here are your job alert results.

---------------------------------------------------------

Self-Help/Customer Support

Alba

Oslo, Oslo, Norway

View job: https://www.linkedin.com/comm/jobs/view/4449044540?alertAction=markasviewed

---------------------------------------------------------

Teknisk Partneransvarlig

CURRENT

Oslo, Oslo, Norway

This company is actively hiring

Apply with resume & profile

View job: https://www.linkedin.com/comm/jobs/view/4447352834?alertAction=markasviewed

---------------------------------------------------------

Fagleder Service Desk

Storebrand

Oslo, Norway

1 company alumni

View job: https://www.linkedin.com/comm/jobs/view/4412425704?alertAction=markasviewed

---------------------------------------------------------

See all jobs: https://www.linkedin.com/comm/jobs/search-results/?keywords=x
"""


def test_parses_all_three_jobs_with_correct_titles():
    entries = parse_digest(REAL_DIGEST_EXCERPT)
    assert [e["job_id"] for e in entries] == ["4449044540", "4447352834", "4412425704"]
    assert entries[0]["title"] == "Self-Help/Customer Support"
    assert entries[1]["title"] == "Teknisk Partneransvarlig"
    assert entries[2]["title"] == "Fagleder Service Desk"


def test_intro_text_before_first_rule_is_not_parsed_as_a_job():
    """The email's own preamble ("Alex, your job alert...") sits before the
    first "---" block separator and has no "View job:" line — must not be
    mistaken for a job title just because it's the first text in the mail."""
    entries = parse_digest(REAL_DIGEST_EXCERPT)
    assert len(entries) == 3


def test_badge_line_between_location_and_view_job_is_skipped():
    entries = parse_digest(REAL_DIGEST_EXCERPT)
    assert entries[1]["employer"] == "CURRENT"
    assert entries[1]["location"] == "Oslo, Oslo, Norway"


def test_see_all_jobs_link_is_not_parsed_as_a_job():
    """The footer "See all jobs" link points at /jobs/search-results/, not
    /jobs/view/{id} — VIEW_JOB_RE must not match it, and even without a
    matching URL there's no ", Norway"-ending line above it either."""
    entries = parse_digest(REAL_DIGEST_EXCERPT)
    assert len(entries) == 3


def test_strip_employer_suffix_removes_embedded_headline_tail():
    """Live case 2026-08-10: Politiets IT-enhet digest title was one line
    "{real title} - Politiets IT-enhet - Søknadsfrist: mandag 17. august
    2026" — everything from " - {employer}" onward must go, so the stored
    title matches the same job's NAV/Jobbnorge copy for dedup/lending."""
    title = (
        "Er du applikasjonstekniker og vil bidra til enda bedre "
        "IT-systemer i politiet? - Politiets IT-enhet - Søknadsfrist: "
        "mandag 17. august 2026"
    )
    assert _strip_employer_suffix(title, "Politiets IT-enhet") == (
        "Er du applikasjonstekniker og vil bidra til enda bedre IT-systemer i politiet?"
    )


def test_strip_employer_suffix_leaves_plain_title_untouched():
    assert _strip_employer_suffix("Fagleder Service Desk", "Storebrand") == "Fagleder Service Desk"


def test_parse_digest_strips_embedded_headline_tail_from_title():
    block = """\
Er du applikasjonstekniker og vil bidra til enda bedre IT-systemer i politiet? - Politiets IT-enhet - Søknadsfrist: mandag 17. august 2026

Politiets IT-enhet

Oslo, Oslo, Norway

3 company alumni

View job: https://www.linkedin.com/comm/jobs/view/4442576715?alertAction=markasviewed
"""
    entries = parse_digest(block)
    assert len(entries) == 1
    assert entries[0]["title"] == (
        "Er du applikasjonstekniker og vil bidra til enda bedre IT-systemer i politiet?"
    )


def test_to_vacancy_row_municipal_from_three_part_location():
    entry = {"job_id": "4449044540", "title": "Self-Help/Customer Support",
              "employer": "Alba", "location": "Oslo, Oslo, Norway"}
    row = to_vacancy_row(entry, {"OSLO": "Oslo"})
    assert row["municipal"] == "Oslo"
    assert row["county"] == "Oslo"
    assert row["uuid"] == "linkedin-4449044540"
    assert row["application_url"] == "https://www.linkedin.com/jobs/view/4449044540"
    assert row["description"] is None


def test_to_vacancy_row_municipal_from_two_part_location_no_county_segment():
    """"Storebrand · Oslo, Norway" — only 2 comma-parts, unlike the usual
    "City, County, Norway" — the kommune is still the first segment."""
    entry = {"job_id": "4412425704", "title": "Fagleder Service Desk",
              "employer": "Storebrand", "location": "Oslo, Norway"}
    row = to_vacancy_row(entry, {"OSLO": "Oslo"})
    assert row["municipal"] == "Oslo"
    assert row["county"] == "Oslo"


def test_to_vacancy_row_county_none_when_unresolved():
    entry = {"job_id": "1", "title": "X", "employer": "Y", "location": "Nowhereville, Norway"}
    row = to_vacancy_row(entry, {"OSLO": "Oslo"})
    assert row["county"] is None
