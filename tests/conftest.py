from collections.abc import Callable
from datetime import date
from email.message import EmailMessage
from io import BytesIO
from pathlib import Path

import pytest
from openpyxl import Workbook
from PIL import Image

from sdi.extraction.schema import ActiveIngredient, Quotation, QuotationLine
from sdi.fx import FxRate
from sdi.loaders import DocumentContent
from sdi.read_model import ReadModel
from sdi.store import Store


class FakeExtractor:
    """Returns a canned outcome per Source file name and records what it was given.

    An outcome is a Quotation, an exception to raise, or a list of those consumed one
    call at a time (to simulate a failure followed by a retry).
    """

    def __init__(self, outcomes: dict[str, object]):
        self._outcomes = {
            name: list(o) if isinstance(o, list) else [o] for name, o in outcomes.items()
        }
        self.received: list[DocumentContent] = []

    def __call__(self, content: DocumentContent) -> Quotation:
        self.received.append(content)
        queue = self._outcomes[Path(content.source_path).name]
        outcome = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def calls_for(self, name: str) -> int:
        return sum(1 for c in self.received if Path(c.source_path).name == name)


class InMemoryFxSource:
    def __init__(self, rates: dict[tuple[str, date], FxRate] | None = None):
        self._rates = rates or {}
        self.requests: list[tuple[str, date]] = []

    def __call__(self, currency: str, on: date) -> FxRate | None:
        self.requests.append((currency, on))
        return self._rates.get((currency, on))


def make_quotation(*products: str, document_type: str = "quotation") -> Quotation:
    return Quotation(
        document_type=document_type,
        supplier="Synthetic Supplier Ltd",
        quote_ref="Q-1",
        currency="USD",
        lines=[
            QuotationLine(
                active_ingredients=[ActiveIngredient(name_as_written=p, inn=p.lower())],
                pack_size=10,
                pack_price=1.0,
            )
            for p in products
        ],
    )


def make_text_pdf(*lines: str) -> bytes:
    """A one-page PDF with each line drawn as text, built by hand to avoid a writer dependency."""
    escaped = [line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") for line in lines]
    stream = "BT /F1 12 Tf 72 720 Td 14 TL " + " ".join(f"({t}) '" for t in escaped) + " ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R"
        " /Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = "%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
    out += "".join(f"{offset:010d} 00000 n \n" for offset in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n"
    return out.encode("latin-1")


def make_email(plain: str | None, html: str | None) -> bytes:
    message = EmailMessage()
    message["From"] = "Sales <sales@synthetic.example>"
    message["To"] = "buyer@example.com"
    message["Date"] = "Tue, 04 Aug 2026 16:42:11 +0200"
    message["Subject"] = "RE: RFQ-1 quotation"
    if plain is not None:
        message.set_content(plain)
    if html is not None:
        if plain is None:
            message.set_content(html, subtype="html")
        else:
            message.add_alternative(html, subtype="html")
    return message.as_bytes()


def make_workbook(sheets: dict[str, list[list[object]]]) -> bytes:
    """An .xlsx with one sheet per entry, rows written as given (None leaves a cell empty)."""
    workbook = Workbook()
    workbook.remove(workbook.active)
    for title, rows in sheets.items():
        sheet = workbook.create_sheet(title)
        for row in rows:
            sheet.append(row)
    out = BytesIO()
    workbook.save(out)
    return out.getvalue()


def make_image(size: tuple[int, int] = (40, 20)) -> bytes:
    """A small grey PNG standing in for a photographed or scanned page."""
    out = BytesIO()
    Image.new("L", size, color=200).save(out, format="PNG")
    return out.getvalue()


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "sdi.db"


@pytest.fixture
def read_model(db_path: Path) -> ReadModel:
    return ReadModel(db_path)


@pytest.fixture
def store(db_path: Path) -> Store:
    return Store(db_path)


@pytest.fixture
def write_source(tmp_path: Path) -> Callable[[str, str | bytes], Path]:
    folder = tmp_path / "inbox"
    folder.mkdir()

    def write(name: str, content: str | bytes) -> Path:
        path = folder / name
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content)
        return path

    return write
