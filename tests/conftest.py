from collections.abc import Callable
from datetime import date
from pathlib import Path

import pytest

from sdi.extraction.schema import ActiveIngredient, Quotation, QuotationLine
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
    def __init__(self, rates: dict[tuple[str, date], float] | None = None):
        self._rates = rates or {}

    def __call__(self, currency: str, on: date) -> float | None:
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
def write_source(tmp_path: Path) -> Callable[[str, str], Path]:
    folder = tmp_path / "inbox"
    folder.mkdir()

    def write(name: str, text: str) -> Path:
        path = folder / name
        path.write_text(text)
        return path

    return write
