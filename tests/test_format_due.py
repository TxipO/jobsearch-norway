"""format_due normalizes every applicationDue shape actually seen in the DB
(surveyed live 2026-08-08) to dd.mm.yyyy — user-requested after the mixed
formats sitting side by side in the list read as messy."""

from web.app import format_due


def test_iso_with_time_component():
    assert format_due("2026-08-18T00:00:00Z") == "18.08.2026"
    assert format_due("2026-08-09T00:00:00") == "09.08.2026"


def test_iso_date_only():
    assert format_due("2026-08-30") == "30.08.2026"


def test_dd_dash_mm_dash_yyyy():
    assert format_due("28-08-2026") == "28.08.2026"


def test_unpadded_d_m_yyyy():
    assert format_due("31.8.2026") == "31.08.2026"


def test_already_dd_dot_mm_dot_yyyy_unchanged():
    assert format_due("15.08.2026") == "15.08.2026"


def test_free_text_passes_through_unchanged():
    assert format_due("Snarest") == "Snarest"
    assert format_due("Vi vurderer kandidater fortløpende!") == "Vi vurderer kandidater fortløpende!"
    assert format_due("23. august (Men vi intervjuer fortløpende i august – søk ASAP!)") == \
        "23. august (Men vi intervjuer fortløpende i august – søk ASAP!)"


def test_none_and_empty():
    assert format_due(None) is None
    assert format_due("") is None
