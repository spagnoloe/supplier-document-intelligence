"""Image Sources (ADR-0004): fields can't be evidence-checked, so a critical field must be
verified by arithmetic or by a second read that agrees, observed through ingest."""

from conftest import FakeExtractor, InMemoryFxSource, make_image

from sdi.extraction.schema import ActiveIngredient, Quotation, QuotationLine
from sdi.pipeline import ingest


def make_line(**overrides) -> QuotationLine:
    """Ten boxes of 100 capsules at 12.50 a box: the line total cross-checks the prices."""
    values = {
        "active_ingredients": [
            ActiveIngredient(
                name_as_written="Amoxicillin",
                inn="amoxicillin",
                strength_value=500,
                strength_unit="mg",
            )
        ],
        "dosage_form": "Capsule",
        "uom": "capsule",
        "pack_size": 100,
        "pack_price": 12.5,
        "quantity": 10,
        "line_total": 125.0,
    }
    return QuotationLine(**{**values, **overrides})


def make_quotation(*lines: QuotationLine) -> Quotation:
    return Quotation(
        document_type="quotation",
        supplier="Synthetic Pharma Ltd",
        quote_ref="Q-1",
        currency="USD",
        lines=list(lines),
    )


def reasons_of(line) -> set[tuple[str, str | None]]:
    return {(r.code, r.field) for r in line.review_reasons}


def results_of(read_model, line) -> dict[str, str]:
    return {f.field: f.result for f in read_model.line_detail(line.id).fields}


def test_critical_fields_unverified_on_an_image_force_review(write_source, store, read_model):
    source = write_source("scan.png", make_image())
    ingest(
        [source],
        FakeExtractor({"scan.png": make_quotation(make_line())}),
        InMemoryFxSource(),
        store,
    )

    [line] = read_model.review_queue()
    assert line.review_status == "needs_review"
    results = results_of(read_model, line)
    # The line total verifies the fields it was calculated from; nothing checks the rest.
    assert {f for f, r in results.items() if r == "verified"} == {
        "pack_price",
        "quantity",
        "line_total",
    }
    assert reasons_of(line) == {
        ("UNVERIFIED_IMAGE_VALUE", "active_ingredients"),
        ("UNVERIFIED_IMAGE_VALUE", "uom"),
        ("UNVERIFIED_IMAGE_VALUE", "pack_size"),
        ("UNVERIFIED_IMAGE_VALUE", "currency"),
    }
