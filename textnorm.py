"""Shared text normalisation, kept in its own tiny module so db.py and
hard_blocks.py can both use it without importing each other (hard_blocks has
no db dependency and shouldn't gain one just for this)."""

import re
import unicodedata

_INLINE_SPACE_RE = re.compile(r"[ \t\xa0]+")


def normalize_text(text: str | None) -> str:
    """NFC + collapse runs of horizontal whitespace (incl. NBSP) to one
    space, keeping newlines (the clause machinery needs them). Added
    2026-10-05 (/fullreview deep): a decomposed "å"/"ø" (NFD, e.g. from a
    pasted/Mac-origin title) or a double/non-breaking space ("Lager\xa0
    medarbeider", "Vi\xa0trenger") silently defeated patterns that contain a
    literal space or a precomposed letter. Idempotent. Single implementation
    (review 2026-10-05) for db.strip_html (description text) and
    hard_blocks.check_exclusion (title + body)."""
    return _INLINE_SPACE_RE.sub(" ", unicodedata.normalize("NFC", text or ""))
