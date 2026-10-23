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


def amoxicillin(pack_price: float = 12.5, **overrides) -> QuotationLine:
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
                "pack_price": f"{pack_price:.2f}",
            }.items()
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
