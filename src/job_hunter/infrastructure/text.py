"""Простейший HTML → text на stdlib (для описаний из источников без plain-text)."""

import re
from html.parser import HTMLParser

_BLOCK_TAGS = frozenset({"br", "p", "li", "div", "ul", "ol", "h1", "h2", "h3", "h4", "tr"})
_SKIP_TAGS = frozenset({"script", "style"})


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self.parts.append(data)


def strip_html(html: str) -> str:
    extractor = _TextExtractor()
    extractor.feed(html)
    text = "".join(extractor.parts)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)
