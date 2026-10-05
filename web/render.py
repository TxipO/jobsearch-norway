import bleach

ALLOWED_TAGS = ["p", "br", "ul", "ol", "li", "strong", "em", "b", "i", "h1", "h2", "h3", "h4", "a"]
ALLOWED_ATTRS = {"a": ["href"]}


def _noopener(attrs, new=False):
    """target=_blank links in employer-supplied descriptions need
    noopener/noreferrer so the opened page can't reach window.opener or see
    our URL (fullreview deep, 2026-10-05). bleach's built-in nofollow
    callback only sets rel="nofollow"; ours runs after it and extends it."""
    if (None, "target") in attrs and attrs[(None, "target")] == "_blank":
        attrs[(None, "rel")] = "nofollow noopener noreferrer"
    return attrs


def sanitize_description(html: str | None) -> str:
    if not html:
        return ""
    cleaned = bleach.clean(html, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS, strip=True)
    return bleach.linkify(cleaned, callbacks=[bleach.callbacks.nofollow, bleach.callbacks.target_blank, _noopener])
