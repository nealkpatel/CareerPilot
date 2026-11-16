"""Turn HTML into readable plain text without external dependencies."""

import html as H
import re
from html.parser import HTMLParser

_BLOCK = {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "tr", "table", "header", "footer"}
_SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form", "button", "iframe", "template"}


class _Text(HTMLParser):
    def __init__(self, skip_chrome=True):
        super().__init__(convert_charrefs=True)
        self.out = []
        self.skip = 0
        self.skip_tags = _SKIP if skip_chrome else {"script", "style", "noscript", "svg", "template"}

    def handle_starttag(self, tag, attrs):
        if tag in self.skip_tags:
            self.skip += 1
        elif tag == "li" and not self.skip:
            self.out.append("\n• ")
        elif tag in _BLOCK and not self.skip:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in self.skip_tags and self.skip:
            self.skip -= 1
        elif tag in _BLOCK and not self.skip:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def html_to_text(markup, skip_chrome=False):
    if not markup:
        return ""
    if "<" not in markup and "&lt;" in markup:
        markup = H.unescape(markup)
    parser = _Text(skip_chrome=skip_chrome)
    try:
        parser.feed(markup)
        parser.close()
    except Exception:
        return re.sub(r"<[^>]+>", " ", markup)
    text = "".join(parser.out)
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
