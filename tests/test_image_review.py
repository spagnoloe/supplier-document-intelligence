"""Image Sources (ADR-0004): fields can't be evidence-checked, so a critical field must be
verified by arithmetic or by a second read that agrees, observed through ingest."""

import pytest
from conftest import FakeExtractor, InMemoryFxSource, make_image
from langchain_core.exceptions import OutputParserException

from sdi.extraction.schema import (
    ActiveIngredient,
    LineFieldEvidence,
    Quotation,
    QuotationFieldEvidence,
    QuotationLine,
)
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


def ingest_scan(write_source, store, first: Quotation, second: Quotation | Exception):
    source = write_source("scan.png", make_image())
    second_reader = FakeExtractor({"scan.png": second})
    ingest(
        [source],
        FakeExtractor({"scan.png": first}),
        InMemoryFxSource(),
        store,
        second_reader=second_reader,
    )
    return second_reader


def test_image_line_whose_two_reads_agree_is_auto_ok(write_source, store, read_model):
    ingest_scan(write_source, store, make_quotation(make_line()), make_quotation(make_line()))

    [line] = read_model.review_queue()
    assert (line.review_status, line.review_reasons) == ("auto_ok", [])
    results = results_of(read_model, line)
    # The dosage form is a term the model standardizes, so reads agreeing on it prove nothing.
    assert results.pop("dosage_form") == "unverified"
    assert set(results.values()) == {"verified"}


def test_fields_the_two_reads_disagree_on_fail_with_second_read_disagreement(
    write_source, store, read_model
):
    # The same UOM written differently, and the dosage form standardized to another term.
    second = make_line(pack_price=12.0, uom="Capsule", dosage_form="Hard capsule")
    ingest_scan(write_source, store, make_quotation(make_line()), make_quotation(second))

    [line] = read_model.review_queue()
    assert line.review_status == "needs_review"
    assert reasons_of(line) == {("SECOND_READ_DISAGREEMENT", "pack_price")}
    [reason] = line.review_reasons
    assert reason.message == "Second read gives 12, first read 12.5"
    results = results_of(read_model, line)
    assert (results["pack_price"], results["uom"]) == ("failed", "verified")


def test_a_value_one_read_leaves_empty_is_not_confirmed_by_the_other(
    write_source, store, read_model
):
    # A price hidden under glare: the first read leaves it empty, the second reads it.
    first = make_line(pack_price=None, quantity=None, line_total=None)
    second = make_line(quantity=None, line_total=None)
    ingest_scan(write_source, store, make_quotation(first), make_quotation(second))

    [line] = read_model.review_queue()
    assert line.review_status == "needs_review"
    assert ("SECOND_READ_DISAGREEMENT", "pack_price") not in reasons_of(line)
    assert line.pack_price is None
    assert results_of(read_model, line)["pack_price"] == "failed"  # still missing


def test_reads_with_different_line_counts_compare_no_line(write_source, store, read_model):
    first = make_quotation(make_line(), make_line(brand_name="Second product"))
    ingest_scan(write_source, store, first, make_quotation(make_line()))

    lines = read_model.review_queue()
    assert {line.review_status for line in lines} == {"needs_review"}
    for line in lines:
        assert ("SECOND_READ_DISAGREEMENT", None) in reasons_of(line)
        assert results_of(read_model, line)["uom"] == "unverified"


TEXT_SOURCE = "Quotation Q-1, prices in USD. Amoxicillin 500 mg capsules, box of 100: 12.50."

TEXT_EVIDENCE = {
    "active_ingredients": "Amoxicillin 500 mg",
    "dosage_form": "capsules",
    "uom": "capsules",
    "pack_size": "box of 100",
    "pack_price": "12.50",
}


def text_line(**overrides) -> QuotationLine:
    evidence = [
        LineFieldEvidence(field=f, span=s, certainty="stated") for f, s in TEXT_EVIDENCE.items()
    ]
    return make_line(quantity=None, line_total=None, evidence=evidence, **overrides)


def text_quotation(*lines: QuotationLine) -> Quotation:
    quotation = make_quotation(*lines)
    quotation.evidence = [QuotationFieldEvidence(field="currency", span="USD", certainty="stated")]
    return quotation


def test_text_source_whose_checks_pass_gets_no_second_read(write_source, store, read_model):
    source = write_source("offer.json", f'"{TEXT_SOURCE}"')
    second_reader = FakeExtractor({})

    ingest(
        [source],
        FakeExtractor({"offer.json": text_quotation(text_line())}),
        InMemoryFxSource(),
        store,
        second_reader=second_reader,
    )

    [line] = read_model.review_queue()
    assert line.review_status == "auto_ok"
    assert second_reader.received == []


def test_text_source_second_read_compares_only_lines_whose_critical_checks_failed(
    write_source, store, read_model
):
    source = write_source("offer.json", f'"{TEXT_SOURCE}"')
    first = text_quotation(text_line(), text_line(pack_size=None, brand_name="Moxi"))
    # The second read differs on both lines; only the failed line is compared.
    second = text_quotation(text_line(uom="tablet"), text_line(brand_name="Maxi"))
    second_reader = FakeExtractor({"offer.json": second})

    ingest(
        [source],
        FakeExtractor({"offer.json": first}),
        InMemoryFxSource(),
        store,
        second_reader=second_reader,
    )

    clean, failed = read_model.quotation_lines()
    assert (clean.review_status, clean.review_reasons) == ("auto_ok", [])
    assert ("SECOND_READ_DISAGREEMENT", "brand_name") in reasons_of(failed)
    assert len(second_reader.received) == 1


def test_line_detail_references_the_source_image_only_for_image_lines(
    write_source, store, read_model
):
    scan = write_source("scan.png", make_image())
    text = write_source("offer.json", f'"{TEXT_SOURCE}"')
    extractor = FakeExtractor(
        {"scan.png": make_quotation(make_line()), "offer.json": text_quotation(text_line())}
    )

    ingest([scan, text], extractor, InMemoryFxSource(), store)

    details = {
        line.source: read_model.line_detail(line.id) for line in read_model.quotation_lines()
    }
    assert details[str(scan)].source_image == str(scan)
    assert details[str(text)].source_image is None


@pytest.mark.parametrize(
    "error, attempts",
    [
        pytest.param(RuntimeError("quota exceeded"), 1, id="model unavailable"),
        pytest.param(OutputParserException("invalid JSON"), 3, id="malformed every attempt"),
    ],
)
def test_failed_second_read_keeps_the_first_read_with_a_note(
    write_source, store, read_model, error, attempts
):
    second_reader = ingest_scan(write_source, store, make_quotation(make_line()), error)

    [status] = read_model.document_statuses()
    assert (status.status, status.line_count) == ("processed", 1)
    [line] = read_model.review_queue()
    assert line.review_status == "needs_review"
    assert ("SECOND_READ_UNAVAILABLE", None) in reasons_of(line)
    assert ("UNVERIFIED_IMAGE_VALUE", "uom") in reasons_of(line)
    [note] = [r for r in line.review_reasons if r.code == "SECOND_READ_UNAVAILABLE"]
    assert str(error) in note.message
    assert len(second_reader.received) == attempts


def test_reads_listing_products_in_a_different_order_compare_no_line(
    write_source, store, read_model
):
    paracetamol = [
        ActiveIngredient(
            name_as_written="Paracetamol", inn="paracetamol", strength_value=500, strength_unit="mg"
        )
    ]
    first = make_quotation(make_line(), make_line(active_ingredients=paracetamol, pack_price=3.0))
    second = make_quotation(make_line(active_ingredients=paracetamol, pack_price=3.0), make_line())
    ingest_scan(write_source, store, first, second)

    for line in read_model.review_queue():
        # Paired by position, every field would fail; the reads aren't compared instead.
        assert "SECOND_READ_DISAGREEMENT" not in {r.code for r in line.review_reasons if r.field}
        assert ("SECOND_READ_DISAGREEMENT", None) in reasons_of(line)
        assert results_of(read_model, line)["uom"] == "unverified"


def test_a_misread_strength_is_a_disagreement_not_a_different_product(
    write_source, store, read_model
):
    misread = [
        ActiveIngredient(
            name_as_written="Amoxicillin", inn="amoxicillin", strength_value=250, strength_unit="mg"
        )
    ]
    second = make_quotation(make_line(active_ingredients=misread))
    ingest_scan(write_source, store, make_quotation(make_line()), second)

    [line] = read_model.review_queue()
    assert reasons_of(line) == {("SECOND_READ_DISAGREEMENT", "active_ingredients")}


def test_source_images_are_the_pages_the_model_was_given(write_source, store, read_model):
    image = make_image()
    scan = write_source("scan.png", image)
    ingest(
        [scan], FakeExtractor({"scan.png": make_quotation(make_line())}), InMemoryFxSource(), store
    )

    [line] = read_model.quotation_lines()
    detail = read_model.line_detail(line.id)

    assert detail.source_image is not None
    assert read_model.source_images(detail.source_image) == [image]
