"""Strip chat-UI chrome that sometimes gets scraped into generated text."""

import re

_CHROME_TOKEN = r"text|copy|download"
_CHROME_LINE = re.compile(
    rf"^(?:{_CHROME_TOKEN})(?:\s+(?:{_CHROME_TOKEN}))*$",
    re.IGNORECASE,
)
_CHROME_ONLY_P = re.compile(
    rf"(?is)^\s*<p\b[^>]*>\s*(?:(?:{_CHROME_TOKEN})(?:\s*<br\s*/?>\s*|\s+))*(?:{_CHROME_TOKEN})\s*</p>\s*"
)
_CHROME_PREFIX_IN_P = re.compile(
    rf"(?is)^(\s*<p\b[^>]*>)\s*(?:(?:{_CHROME_TOKEN})\s*(?:<br\s*/?>\s*)?)+"
)


def strip_scraped_ui_chrome(text):
    """Remove leading 'text' / 'Copy' / 'Download' lines from plain text."""
    if not text:
        return text or ""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if not stripped or _CHROME_LINE.match(stripped):
            i += 1
            continue
        break
    return "\n".join(lines[i:]).strip()


def _strip_leading_chrome_paragraphs(html):
    prev = None
    while prev != html:
        prev = html
        html = _CHROME_ONLY_P.sub("", html, count=1)
    html = _CHROME_PREFIX_IN_P.sub(r"\1", html, count=1)
    return html


def strip_scraped_ui_chrome_html(html):
    """Remove leading text/Copy/Download paragraphs from stored HTML."""
    if not html:
        return html or ""
    match = re.search(r"(?is)(<body\b[^>]*>)(.*?)(</body>)", html)
    if match:
        body = _strip_leading_chrome_paragraphs(match.group(2))
        return html[: match.start()] + match.group(1) + body + match.group(3) + html[match.end() :]
    return _strip_leading_chrome_paragraphs(html)
