"""Verification and review status (ADR-0004), observed through ingest and the read model."""

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
        "currency": "USD",
        "items": [
            {
                "description": "Amoxicillin 500 mg hard capsules",
                "pack": "Box of 100",
                "box_price": "1,250.00",
            }
        ],
    }
)

LINE_EVIDENCE = {
    "active_ingredients": "Amoxicillin 500 mg",
    "dosage_form": "hard capsules",
    "uom": "capsules",
    "pack_size": "Box of 100",
    "pack_price": "1,250.00",
}


def evidence(spans: dict[str, str | None], certainty: dict[str, str] | None = None):
    certainty = certainty or {}
    return [
        LineFieldEvidence(field=f, span=s, certainty=certainty.get(f, "stated"))
        for f, s in spans.items()
    ]


def make_line(spans: dict[str, str | None] | None = None, **overrides) -> QuotationLine:
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
        "pack_price": 1250.0,
        "evidence": evidence(LINE_EVIDENCE if spans is None else spans),
    }
    return QuotationLine(**{**values, **overrides})


def make_quotation(*lines: QuotationLine, currency: str | None = "USD") -> Quotation:
    return Quotation(
        document_type="quotation",
        supplier="Synthetic Pharma Ltd",
        quote_ref="Q-1",
        currency=currency,
        lines=list(lines),
        evidence=[QuotationFieldEvidence(field="currency", span=currency, certainty="stated")],
    )


def ingest_one(write_source, store, quotation: Quotation, text: str = SOURCE):
    source = write_source("offer.json", text)
    ingest([source], FakeExtractor({"offer.json": quotation}), InMemoryFxSource(), store)
    return source


def test_fully_evidenced_line_is_auto_ok_with_every_field_verified(write_source, store, read_model):
    ingest_one(write_source, store, make_quotation(make_line()))

    [line] = read_model.review_queue()
    assert (line.review_status, line.review_reasons) == ("auto_ok", [])
    detail = read_model.line_detail(line.id)
    assert {f.field: f.result for f in detail.fields} == {
        "active_ingredients": "verified",
        "dosage_form": "verified",
        "uom": "verified",
        "pack_size": "verified",
        "pack_price": "verified",
        "currency": "verified",
    }
    pack_price = next(f for f in detail.fields if f.field == "pack_price")
    assert (pack_price.value, [e.span for e in pack_price.evidence]) == (1250.0, ["1,250.00"])
