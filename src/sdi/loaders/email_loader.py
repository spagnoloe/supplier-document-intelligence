import re
from email import policy
from email.parser import BytesParser
from html.parser import HTMLParser
from pathlib import Path

from sdi.loaders import DocumentContent, register

HEADERS = ("From", "Date", "Subject")


@register(".eml")
def load_email(path: Path) -> DocumentContent:
    with path.open("rb") as f:
        message = BytesParser(policy=policy.default).parse(f)
    # A multipart/alternative message carries the same content more than once; only one
    # body is kept so the model doesn't see every line twice. Plain text is preferred as
    # the sender's own rendering; HTML is the fallback for HTML-only mail.
    body = message.get_body(preferencelist=("plain", "html"))
    sections = ["\n".join(f"{name}: {message[name]}" for name in HEADERS if message[name])]
    if body is not None:
        content = body.get_content()
        sections.append(html_to_text(content) if body.get_content_subtype() == "html" else content)
    return DocumentContent(
        source_path=str(path),
        text="\n\n".join(sections),
        metadata={"file_type": "eml"},
    )


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    return parser.text()


class _TextExtractor(HTMLParser):
    """Keeps the reading order of an HTML body: blocks on their own lines, each table row on
    one line with cells separated by " | ", so a price stays next to its product."""

    BLOCKS = {"p", "div", "br", "tr", "li", "table", "h1", "h2", "h3", "h4", "h5", "h6"}
    CELLS = {"td", "th"}
    HIDDEN = {"style", "script", "head", "title"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._lines: list[str] = [""]
        self._hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.HIDDEN:
            self._hidden_depth += 1
        elif tag in self.BLOCKS:
            self._lines.append("")
        elif tag in self.CELLS and self._lines[-1].strip():
            self._lines[-1] = self._lines[-1].rstrip() + " | "

    def handle_endtag(self, tag: str) -> None:
        if tag in self.HIDDEN:
            self._hidden_depth = max(0, self._hidden_depth - 1)
        elif tag in self.BLOCKS:
            self._lines.append("")

    def handle_data(self, data: str) -> None:
        if not self._hidden_depth:
            # HTML whitespace is not significant: source line wraps become spaces.
            self._lines[-1] += re.sub(r"\s+", " ", data)

    def text(self) -> str:
        lines = [line.strip() for line in self._lines]
        return "\n".join(line for line in lines if line)
