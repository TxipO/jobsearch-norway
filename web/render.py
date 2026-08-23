import bleach

ALLOWED_TAGS = ["p", "br", "ul", "ol", "li", "strong", "em", "b", "i", "h1", "h2", "h3", "h4", "a"]
ALLOWED_ATTRS = {"a": ["href"]}


def sanitize_description(html: str | None) -> str:
    if not html:
        return ""
    cleaned = bleach.clean(html, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS, strip=True)
    return bleach.linkify(cleaned, callbacks=[bleach.callbacks.nofollow, bleach.callbacks.target_blank])
