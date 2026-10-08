"""Regression test built from two real LinkedIn job-alert digest emails
(auto-forwarded via the Gmail filter, jobalerts-noreply@linkedin.com ->
the address gmail_client.py reads, 2026-08-09/08-10). The fixture below is a trimmed
excerpt of the real plain-text MIME body — tracking query params
shortened, but the dash-separated block structure (title / employer /
location / optional badge line(s) / "View job: {url}") is untouched,
since that structure is exactly what parse_digest() depends on."""

import pytest

from linkedin_client import _resolve_location, _strip_employer_suffix, parse_digest, to_vacancy_row

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
    /jobs/view/{id} — VIEW_JOB_RE must not match it, and the block has no
    "View job:" line at all, so there is no card to read."""
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


def test_sync_reports_messages_and_warns_when_digest_format_drifted(tmp_path, monkeypatch):
    """Fetched messages but 0 parsed used to read {"parsed": 0, "upserted": 0},
    identical to an idle day (fullreview Stage 2 item 12, 2026-10-05)."""
    import db
    import linkedin_client

    conn = db.connect(tmp_path / "t.db")
    monkeypatch.setattr(linkedin_client, "_build_municipality_county_map", lambda: {})

    monkeypatch.setattr(linkedin_client, "fetch_digest_texts", lambda: ["a brand new digest layout"])
    stats = linkedin_client.sync(conn)
    assert stats["messages"] == 1 and stats["parsed"] == 0
    assert "warning" in stats

    monkeypatch.setattr(linkedin_client, "fetch_digest_texts", lambda: [])
    assert linkedin_client.sync(conn) == {"messages": 0, "cards": 0, "parsed": 0, "upserted": 0}

    monkeypatch.setattr(linkedin_client, "fetch_digest_texts", lambda: [REAL_DIGEST_EXCERPT])
    stats = linkedin_client.sync(conn)
    assert stats["messages"] == 1 and stats["parsed"] > 0 and "warning" not in stats


# --- Digest layout seen from 2026-08 on (2026-10-08 incident) ---------------
# LinkedIn writes most places bare ("Lindesnes", "Stavanger/Sandnes", "Greater
# Oslo Region") and only some as "Oslo, Norway"; the alert header sits INSIDE
# the first card's block; multi-alert digests add section headers. The parser
# used to require a location ending in ", Norway", so 157 of 201 live digests
# yielded nothing and 276 of 309 distinct jobs never reached the DB. Fixture is
# the real structure with tracking params cut short.
CURRENT_LAYOUT_DIGEST = """Your job alert for IT support in Norway
New jobs match your preferences.

Field Support Technician
HCLTech
Lindesnes
This company is actively hiring
Apply with resume & profile
View job: https://www.linkedin.com/comm/jobs/view/4443915000/?trackingId=a%3D%3D&refId=b

---------------------------------------------------------

Technical Support Specialist - Norway
Easee
Stavanger/Sandnes
View job: https://www.linkedin.com/comm/jobs/view/4453800001/?trackingId=c

---------------------------------------------------------

See all jobs on LinkedIn:  https://www.linkedin.com/comm/jobs/search-results/?keywords=x
New jobs from your other alerts
<strong class="font-bold" style="font-weight: 600;">IT support</strong> jobs in Norway
Senior Support Engineer| OpenText NNMi | Oslo, Norway
Infosys
Greater Oslo Region
2 connections
View job: https://www.linkedin.com/comm/jobs/view/4421100002/?trackingId=d
"""

# The variant seen from mid-September: header and manage link on ONE line, and
# no "match your preferences" line at all.
MANAGE_LINE_DIGEST = """Your job alert for Technical Support Engineer in NorwayManage your job alerts:  https://www.linkedin.com/comm/jobs/alerts?x=1
Technical Support Specialist - Norway
Easee
Stavanger
View job: https://www.linkedin.com/comm/jobs/view/4463253058/?trackingId=e
"""


def test_current_layout_parses_every_card_including_bare_locations():
    entries = parse_digest(CURRENT_LAYOUT_DIGEST)
    assert [(e["title"], e["employer"], e["location"]) for e in entries] == [
        ("Field Support Technician", "HCLTech", "Lindesnes"),
        ("Technical Support Specialist - Norway", "Easee", "Stavanger/Sandnes"),
        ("Senior Support Engineer| OpenText NNMi | Oslo, Norway", "Infosys", "Greater Oslo Region"),
    ]
    assert [e["job_id"] for e in entries] == ["4443915000", "4453800001", "4421100002"]


def test_alert_header_inside_the_first_card_block_is_not_the_title():
    first = parse_digest(CURRENT_LAYOUT_DIGEST)[0]
    assert first["title"] == "Field Support Technician"


def test_multi_alert_section_headers_never_leak_into_a_card():
    """"See all jobs on LinkedIn: <url>", "New jobs from your other alerts" and
    the <strong>…</strong> "jobs in Norway" line precede the first card of a
    section. Taken as title/employer/location they produced rows titled
    "See all jobs on LinkedIn" in the prototype."""
    for e in parse_digest(CURRENT_LAYOUT_DIGEST):
        for field in ("title", "employer", "location"):
            assert "http" not in e[field] and "<" not in e[field]
            assert not e[field].startswith(("See all", "New jobs from"))


def test_single_line_header_with_manage_link_is_skipped():
    entries = parse_digest(MANAGE_LINE_DIGEST)
    assert len(entries) == 1
    assert entries[0]["title"] == "Technical Support Specialist - Norway"
    assert entries[0]["location"] == "Stavanger"


@pytest.mark.parametrize("raw, expected", [
    ("Oslo, Norway", ("Oslo", "Oslo")),
    ("Drammen, Viken, Norway", ("Drammen", "Buskerud")),
    ("Bergen", ("Bergen", "Vestland")),
    ("Greater Oslo Region", ("Oslo", "Oslo")),
    ("Trondheim Region", ("Trondheim", "Trøndelag")),
    ("Stavanger/Sandnes", ("Stavanger", "Rogaland")),
    ("Vestland, Norway", ("Vestland", "Vestland")),
    ("Norway", (None, None)),
    ("Nowhereville", ("Nowhereville", None)),
])
def test_resolve_location_handles_every_form_linkedin_writes(raw, expected):
    mc = {"OSLO": "Oslo", "DRAMMEN": "Buskerud", "BERGEN": "Vestland", "TRONDHEIM": "Trøndelag",
          "STAVANGER": "Rogaland", "SANDNES": "Rogaland", "VESTLAND": "Vestland"}
    assert _resolve_location(raw, mc) == expected


def test_nationwide_location_leaves_municipal_null_not_the_word_norway():
    entry = {"job_id": "1", "title": "X", "employer": "Y", "location": "Norway"}
    row = to_vacancy_row(entry, {"OSLO": "Oslo"})
    assert row["municipal"] is None and row["county"] is None


def test_sync_warns_when_cards_exist_that_the_parser_cannot_read(tmp_path, monkeypatch):
    """The 2026-10-08 failure was invisible because a few old-format mails still
    parsed, so "0 entries parsed" never fired. Cards are now counted straight
    from the "View job:" lines: any card that does not become an entry is a
    visible gap."""
    import db
    import linkedin_client

    conn = db.connect(tmp_path / "t.db")
    monkeypatch.setattr(linkedin_client, "_build_municipality_county_map", lambda: {})
    unreadable = "Only Title\nView job: https://www.linkedin.com/comm/jobs/view/4400000009/?x=1\n"
    monkeypatch.setattr(linkedin_client, "fetch_digest_texts", lambda: [CURRENT_LAYOUT_DIGEST, unreadable])
    stats = linkedin_client.sync(conn)
    assert stats["cards"] == 4 and stats["parsed"] == 3 and stats["upserted"] == 3
    assert "1 of 4" in stats["warning"]
