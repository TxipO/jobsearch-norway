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
