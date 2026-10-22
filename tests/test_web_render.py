"""sanitize_description (bleach) strips active content from employer-supplied
HTML while keeping basic formatting."""

import pytest

from web.render import sanitize_description


@pytest.mark.parametrize("dirty, forbidden", [
    ("<script>alert(1)</script><p>ok</p>", "<script"),
    ("<img src=x onerror=alert(1)>t", "onerror"),
    ("<p onclick=\"x()\">p</p>", "onclick"),
    ("<a href=\"javascript:alert(1)\">x</a>", "javascript"),
    ("<a href=\" JaVa\tScRiPt:alert(1)\">x</a>", "script:"),
    ("<a href=\"data:text/html,<script>1</script>\">d</a>", "data:"),
    ("<iframe src=\"//evil.example\"></iframe>y", "iframe"),
    ("<svg onload=alert(1)><circle/></svg>z", "svg"),
    ("<p style=\"background:url(javascript:x)\">p</p>", "style"),
    ("<b style=\"x:expression(alert(1))\">b</b>", "expression"),
    ("<object data=\"x\"></object><embed src=\"x\">", "<object"),
])
def test_active_content_is_stripped(dirty, forbidden):
    assert forbidden not in sanitize_description(dirty).lower()


def test_safe_formatting_and_links_survive():
    out = sanitize_description(
        '<h2>T</h2><p>Hi <strong>x</strong> <em>y</em><br></p><ul><li>a</li></ul>'
        '<a href="https://example.com/a">l</a>'
    )
    for part in ("<h2>T</h2>", "<strong>x</strong>", "<em>y</em>", "<ul><li>a</li></ul>", 'href="https://example.com/a"'):
        assert part in out
    assert "noopener" in out and 'target="_blank"' in out


def test_empty_input():
    assert sanitize_description(None) == "" and sanitize_description("") == ""
