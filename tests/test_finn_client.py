"""Regression test for a live bug found 2026-07-18 while answering a user
question ("does the % compute correctly?"): to_vacancy_row() hardcoded
county=None instead of resolving it, so scoring.py's Vestland fylke bonus
(+7) never applied to finn.no vacancies even when the location clearly was
in Vestland — 50 of 51 active finn.no rows had no county at all."""

from finn_client import to_vacancy_row


def test_county_resolved_from_municipality_map():
    entry = {"title": "Selger", "employer": "Nordspec AS", "location": "Laksevåg",
              "url": "https://www.finn.no/123456"}
    row = to_vacancy_row(entry, {"LAKSEVÅG": "Vestland"})
    assert row["county"] == "Vestland"
    assert row["municipal"] == "Laksevåg"


def test_county_none_when_location_not_in_map():
    """A location the map doesn't recognize (e.g. it wasn't a poststed or
    municipality name the lookup covers) must fall back to None, not raise —
    same "unresolved is shown, not guessed" stance as the rest of scoring."""
    entry = {"title": "Selger", "employer": "X AS", "location": "Nowhereville",
              "url": "https://www.finn.no/999"}
    row = to_vacancy_row(entry, {"LAKSEVÅG": "Vestland"})
    assert row["county"] is None


import db
import finn_client

DASHES = "-" * 30


def _digest(*cards):
    return f"Hei!\n{DASHES}\n" + f"\n{DASHES}\n".join(cards) + f"\n{DASHES}\n"


def test_parse_digest_splits_employer_and_location_on_last_comma():
    text = _digest("Selger\nNordspec AS, Laksevåg\nFlere detaljer: https://www.finn.no/111")
    [e] = finn_client.parse_digest(text)
    assert (e["employer"], e["location"]) == ("Nordspec AS", "Laksevåg")


def test_parse_digest_no_comma_is_employer_only_not_location():
    """2026-10-05 audit: rpartition(",") on "Kiwi" returned ("", "", "Kiwi"),
    so the employer name became the municipality."""
    text = _digest("Butikkmedarbeider\nKiwi\nFlere detaljer: https://www.finn.no/222")
    [e] = finn_client.parse_digest(text)
    assert e["employer"] == "Kiwi"
    assert e["location"] is None
    row = to_vacancy_row(e, {"KIWI": "Wrong"})
    assert row["municipal"] is None and row["county"] is None
    assert row["business_name"] == "Kiwi"


def test_sync_reports_messages_and_warns_when_digest_format_drifted(tmp_path, monkeypatch):
    """Fetched messages but 0 parsed used to read {"parsed": 0, "upserted": 0},
    identical to an idle day (fullreview Stage 2 item 12, 2026-10-05)."""
    conn = db.connect(tmp_path / "t.db")
    monkeypatch.setattr(finn_client, "_build_municipality_county_map", lambda: {})

    monkeypatch.setattr(finn_client, "fetch_digest_texts", lambda: ["a brand new digest layout"])
    stats = finn_client.sync(conn)
    assert stats["messages"] == 1 and stats["parsed"] == 0
    assert "warning" in stats

    # Genuinely idle (no messages): no warning.
    monkeypatch.setattr(finn_client, "fetch_digest_texts", lambda: [])
    stats = finn_client.sync(conn)
    assert stats == {"messages": 0, "parsed": 0, "upserted": 0}

    # Healthy digest: parsed > 0, no warning.
    ok = _digest("Selger\nNordspec AS, Laksevåg\nFlere detaljer: https://www.finn.no/111")
    monkeypatch.setattr(finn_client, "fetch_digest_texts", lambda: [ok])
    stats = finn_client.sync(conn)
    assert stats == {"messages": 1, "parsed": 1, "upserted": 1}
