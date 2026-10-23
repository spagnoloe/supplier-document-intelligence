"""Reconciliation (ADR-0003): Sources of one Quotation linked, their lines compared, observed
through ingest and the read model."""

import json

from conftest import FakeExtractor, InMemoryFxSource

from sdi.extraction.schema import (
    ActiveIngredient,
    LineFieldEvidence,
    Quotation,
    QuotationFieldEvidence,
    QuotationLine,
)
from sdi.pipeline import ingest

SOURCE = json.dumps(
    {
        "supplier": "Synthetic Pharma Ltd",
        "ref": "SP-77",
        "currency": "USD",
        "items": [
            {"description": "Amoxicillin 500 mg capsules", "pack": "Box of 100", "price": "12.50"},
            {"description": "Ibuprofen 400 mg tablets", "pack": "Box of 30", "price": "3.10"},
        ],
    }
)


def amoxicillin(pack_price: float | None = 12.5, **overrides) -> QuotationLine:
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
        "pack_price": pack_price,
        "evidence": [
            LineFieldEvidence(field=f, span=s, certainty="stated")
            for f, s in {
                "active_ingredients": "Amoxicillin 500 mg",
                "dosage_form": "capsules",
                "uom": "capsules",
                "pack_size": "Box of 100",
                "pack_price": None if pack_price is None else f"{pack_price:.2f}",
            }.items()
            if s is not None
        ],
    }
    return QuotationLine(**{**values, **overrides})


def ibuprofen() -> QuotationLine:
    return QuotationLine(
        active_ingredients=[
            ActiveIngredient(
                name_as_written="Ibuprofen",
                inn="ibuprofen",
                strength_value=400,
                strength_unit="mg",
            )
        ],
        dosage_form="Tablet",
        uom="tablet",
        pack_size=30,
        pack_price=3.1,
        evidence=[
            LineFieldEvidence(field=f, span=s, certainty="stated")
            for f, s in {
                "active_ingredients": "Ibuprofen 400 mg",
                "dosage_form": "tablets",
                "uom": "tablets",
                "pack_size": "Box of 30",
                "pack_price": "3.10",
            }.items()
        ],
    )


def quotation(
    *lines: QuotationLine,
    supplier: str | None = "Synthetic Pharma Ltd",
    quote_ref: str | None = "SP-77",
) -> Quotation:
    return Quotation(
        document_type="quotation",
        supplier=supplier,
        quote_ref=quote_ref,
        currency="USD",
        lines=list(lines),
        evidence=[QuotationFieldEvidence(field="currency", span="USD", certainty="stated")],
    )


def ingest_sources(write_source, store, quotations: dict[str, Quotation]) -> None:
    sources = [write_source(name, SOURCE) for name in quotations]
    ingest(sources, FakeExtractor(quotations), InMemoryFxSource(), store)


def test_disagreeing_sources_of_one_quotation_are_a_conflict_on_that_field(
    write_source, store, read_model
):
    text = SOURCE.replace("12.50", "12.50 / 15.20")
    a, b = write_source("a.json", text), write_source("b.json", text)
    quotations = {
        "a.json": quotation(amoxicillin(12.5), ibuprofen()),
        "b.json": quotation(amoxicillin(15.2), ibuprofen()),
    }
    ingest([a, b], FakeExtractor(quotations), InMemoryFxSource(), store)

    [conflict] = read_model.conflicts()
    assert (conflict.supplier, conflict.quote_ref, conflict.field) == (
        "Synthetic Pharma Ltd",
        "SP-77",
        "pack_price",
    )
    assert conflict.inns == ["amoxicillin"]
    assert [(v.source, v.value) for v in conflict.values] == [(str(a), 12.5), (str(b), 15.2)]

    lines = {(line.source, line.inns[0]): line for line in read_model.quotation_lines()}
    for source in (a, b):
        amox = lines[(str(source), "amoxicillin")]
        assert amox.review_status == "needs_review"
        assert ("IN_CONFLICT", "pack_price") in {(r.code, r.field) for r in amox.review_reasons}
        assert lines[(str(source), "ibuprofen")].review_status == "auto_ok"


def test_agreeing_sources_have_no_conflict_even_when_one_value_is_unreadable(
    write_source, store, read_model
):
    ingest_sources(
        write_source,
        store,
        {
            "a.json": quotation(amoxicillin(), ibuprofen()),
            "b.json": quotation(amoxicillin(pack_price=None), ibuprofen()),
        },
    )

    assert read_model.conflicts() == []
    a_lines = [line for line in read_model.quotation_lines() if line.source.endswith("a.json")]
    assert {line.review_status for line in a_lines} == {"auto_ok"}


def test_copies_writing_supplier_and_quote_reference_differently_are_linked(
    write_source, store, read_model
):
    text = SOURCE.replace("12.50", "12.50 / 15.20")
    a, b = write_source("a.json", text), write_source("b.json", text)
    quotations = {
        "a.json": quotation(amoxicillin(12.5)),
        "b.json": quotation(
            amoxicillin(15.2), supplier="SYNTHETIC PHARMA LIMITED", quote_ref="sp 77"
        ),
    }
    ingest([a, b], FakeExtractor(quotations), InMemoryFxSource(), store)

    [conflict] = read_model.conflicts()
    assert [v.value for v in conflict.values] == [12.5, 15.2]


def test_differing_lines_of_different_quotations_are_not_conflicts(write_source, store, read_model):
    text = SOURCE.replace("12.50", "12.50 / 15.20")
    a, b, c = (write_source(name, text) for name in ("a.json", "b.json", "c.json"))
    quotations = {
        "a.json": quotation(amoxicillin(12.5)),
        "b.json": quotation(amoxicillin(15.2), quote_ref="SP-78"),
        "c.json": quotation(amoxicillin(15.2), supplier="Other Pharma Ltd"),
    }
    ingest([a, b, c], FakeExtractor(quotations), InMemoryFxSource(), store)

    assert read_model.conflicts() == []


def test_source_without_quote_reference_is_flagged_and_never_linked(
    write_source, store, read_model
):
    text = SOURCE.replace("12.50", "12.50 / 15.20")
    a, b = write_source("a.json", text), write_source("b.json", text)
    quotations = {
        "a.json": quotation(amoxicillin(12.5)),
        "b.json": quotation(amoxicillin(15.2), quote_ref=None),
    }
    ingest([a, b], FakeExtractor(quotations), InMemoryFxSource(), store)

    assert read_model.conflicts() == []
    lines = {line.source: line for line in read_model.quotation_lines()}
    unlinked = lines[str(b)]
    assert [r.code for r in unlinked.review_reasons] == ["NO_QUOTE_REFERENCE"]
    # A missing reference is a reason to look, not on its own a reason to review.
    assert unlinked.review_status == "auto_ok"
    assert lines[str(a)].review_reasons == []
    assert read_model.review_queue(reason_code="NO_QUOTE_REFERENCE") == [unlinked]


def test_rerun_after_a_source_is_corrected_clears_its_conflict(write_source, store, read_model):
    text = SOURCE.replace("12.50", "12.50 / 15.20")
    a, b = write_source("a.json", text), write_source("b.json", text)
    ingest(
        [a, b],
        FakeExtractor(
            {"a.json": quotation(amoxicillin(12.5)), "b.json": quotation(amoxicillin(15.2))}
        ),
        InMemoryFxSource(),
        store,
    )
    assert len(read_model.conflicts()) == 1

    # Only b is re-ingested; a's line must lose the flag b's old value put on it.
    ingest([b], FakeExtractor({"b.json": quotation(amoxicillin(12.5))}), InMemoryFxSource(), store)

    assert read_model.conflicts() == []
    assert {
        (line.review_status, tuple(line.review_reasons)) for line in read_model.quotation_lines()
    } == {("auto_ok", ())}


def test_product_quoted_twice_matches_by_pack_size_whatever_the_order(
    write_source, store, read_model
):
    text = SOURCE.replace("Box of 100", "Box of 100 / Box of 1000").replace(
        "12.50", "12.50 / 110.00"
    )
    box_of_1000 = {
        "pack_size": 1000,
        "pack_price": 110.0,
        "evidence": [
            LineFieldEvidence(field=f, span=s, certainty="stated")
            for f, s in {"pack_size": "Box of 1000", "pack_price": "110.00"}.items()
        ],
    }
    a, b = write_source("a.json", text), write_source("b.json", text)
    quotations = {
        "a.json": quotation(amoxicillin(), amoxicillin(**box_of_1000)),
        "b.json": quotation(amoxicillin(**box_of_1000), amoxicillin()),
    }
    ingest([a, b], FakeExtractor(quotations), InMemoryFxSource(), store)

    assert read_model.conflicts() == []


def test_micro_sign_strength_matches_mcg_and_never_grams(write_source, store, read_model):
    def strength(unit: str, pack_price: float) -> QuotationLine:
        line = amoxicillin(pack_price)
        line.active_ingredients[0].strength_unit = unit
        return line

    text = SOURCE.replace("12.50", "12.50 / 15.20")
    a, b, c = (write_source(name, text) for name in ("a.json", "b.json", "c.json"))
    quotations = {
        "a.json": quotation(strength("µg", 12.5)),
        "b.json": quotation(strength("mcg", 15.2)),
        "c.json": quotation(strength("g", 15.2)),
    }
    ingest([a, b, c], FakeExtractor(quotations), InMemoryFxSource(), store)

    [conflict] = read_model.conflicts()
    assert [v.source for v in conflict.values] == [str(a), str(b)]


def test_references_differing_only_where_separators_fall_are_not_linked(
    write_source, store, read_model
):
    text = SOURCE.replace("12.50", "12.50 / 15.20")
    a, b = write_source("a.json", text), write_source("b.json", text)
    quotations = {
        "a.json": quotation(amoxicillin(12.5), quote_ref="Q1-23"),
        "b.json": quotation(amoxicillin(15.2), quote_ref="Q12-3"),
    }
    ingest([a, b], FakeExtractor(quotations), InMemoryFxSource(), store)

    assert read_model.conflicts() == []


def test_source_without_supplier_is_flagged_and_never_linked(write_source, store, read_model):
    text = SOURCE.replace("12.50", "12.50 / 15.20")
    a, b = write_source("a.json", text), write_source("b.json", text)
    quotations = {
        "a.json": quotation(amoxicillin(12.5), supplier=None),
        "b.json": quotation(amoxicillin(15.2), supplier=None),
    }
    ingest([a, b], FakeExtractor(quotations), InMemoryFxSource(), store)

    assert read_model.conflicts() == []
    for line in read_model.quotation_lines():
        assert [r.code for r in line.review_reasons] == ["NO_SUPPLIER"]
        assert line.review_status == "auto_ok"


def test_placeholder_reference_without_digits_counts_as_missing(write_source, store, read_model):
    text = SOURCE.replace("12.50", "12.50 / 15.20")
    a, b = write_source("a.json", text), write_source("b.json", text)
    quotations = {
        "a.json": quotation(amoxicillin(12.5), quote_ref="N/A"),
        "b.json": quotation(amoxicillin(15.2), quote_ref="n/a"),
    }
    ingest([a, b], FakeExtractor(quotations), InMemoryFxSource(), store)

    assert read_model.conflicts() == []
    for line in read_model.quotation_lines():
        assert [r.code for r in line.review_reasons] == ["NO_QUOTE_REFERENCE"]
