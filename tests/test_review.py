"""Verification and review status (ADR-0004), observed through ingest and the read model."""

import json

import pytest
from conftest import FakeExtractor, InMemoryFxSource

from sdi.extraction.schema import (
    ActiveIngredient,
    LineFieldEvidence,
    PriceTier,
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


def reasons_of(line) -> set[tuple[str, str | None]]:
    return {(r.code, r.field) for r in line.review_reasons}


def results_of(read_model, line) -> dict[str, str]:
    return {f.field: f.result for f in read_model.line_detail(line.id).fields}


def test_span_missing_from_source_fails_the_field_and_forces_review(
    write_source, store, read_model
):
    spans = {**LINE_EVIDENCE, "pack_price": "1,350.00"}
    ingest_one(write_source, store, make_quotation(make_line(spans)))

    [line] = read_model.review_queue()
    assert line.review_status == "needs_review"
    assert reasons_of(line) == {("EVIDENCE_NOT_FOUND", "pack_price")}
    assert results_of(read_model, line)["pack_price"] == "failed"
    [reason] = line.review_reasons
    assert reason.message == "'1,350.00' not found in source text"


def test_value_not_written_in_its_real_span_fails_evidence(write_source, store, read_model):
    ingest_one(write_source, store, make_quotation(make_line(pack_price=1350.0)))

    [line] = read_model.review_queue()
    assert (line.review_status, reasons_of(line)) == (
        "needs_review",
        {("EVIDENCE_NOT_FOUND", "pack_price")},
    )


def test_field_without_evidence_fails_evidence(write_source, store, read_model):
    spans = {k: v for k, v in LINE_EVIDENCE.items() if k != "pack_size"}
    ingest_one(write_source, store, make_quotation(make_line(spans)))

    [line] = read_model.review_queue()
    assert reasons_of(line) == {("EVIDENCE_NOT_FOUND", "pack_size")}


def test_evidence_matches_despite_whitespace_case_and_number_formatting(
    write_source, store, read_model
):
    spans = {
        **LINE_EVIDENCE,
        "active_ingredients": "AMOXICILLIN  500mg",
        "pack_size": "Box of\n100",
        "pack_price": "1250",
    }
    text = SOURCE.replace("1,250.00", "1.250,00").replace("500 mg", "500 MG")
    ingest_one(write_source, store, make_quotation(make_line(spans)), text)

    [line] = read_model.review_queue()
    assert (line.review_status, line.review_reasons) == ("auto_ok", [])
    assert set(results_of(read_model, line).values()) == {"verified"}


def test_number_span_does_not_match_inside_a_longer_number(write_source, store, read_model):
    spans = {**LINE_EVIDENCE, "pack_price": "250"}
    ingest_one(write_source, store, make_quotation(make_line(spans, pack_price=250.0)))

    [line] = read_model.review_queue()
    assert reasons_of(line) == {("EVIDENCE_NOT_FOUND", "pack_price")}


def test_failed_non_critical_field_is_a_reason_without_forcing_review(
    write_source, store, read_model
):
    spans = {**LINE_EVIDENCE, "brand_name": "Moxilex"}
    ingest_one(write_source, store, make_quotation(make_line(spans, brand_name="Moxilex")))

    [line] = read_model.review_queue()
    assert (line.review_status, reasons_of(line)) == (
        "auto_ok",
        {("EVIDENCE_NOT_FOUND", "brand_name")},
    )
    assert results_of(read_model, line)["brand_name"] == "failed"


def test_currency_that_is_not_iso_4217_fails(write_source, store, read_model):
    text = SOURCE.replace('"USD"', '"US$"')
    ingest_one(write_source, store, make_quotation(make_line(), currency="US$"), text)

    [line] = read_model.review_queue()
    assert (line.review_status, reasons_of(line)) == (
        "needs_review",
        {("INVALID_CURRENCY", "currency")},
    )


def test_line_currency_overrides_and_is_checked_against_line_evidence(
    write_source, store, read_model
):
    spans = {**LINE_EVIDENCE, "currency": "EUR"}
    ingest_one(write_source, store, make_quotation(make_line(spans, currency="EUR")))

    [line] = read_model.review_queue()
    assert line.currency == "EUR"
    assert reasons_of(line) == {("EVIDENCE_NOT_FOUND", "currency")}


def test_zero_price_fails_as_non_positive(write_source, store, read_model):
    text = SOURCE.replace("1,250.00", "0.00")
    spans = {**LINE_EVIDENCE, "pack_price": "0.00"}
    ingest_one(write_source, store, make_quotation(make_line(spans, pack_price=0.0)), text)

    [line] = read_model.review_queue()
    assert (line.review_status, reasons_of(line)) == (
        "needs_review",
        {("NON_POSITIVE_PRICE", "pack_price")},
    )


@pytest.mark.parametrize(
    ("overrides", "dropped_evidence", "expected"),
    [
        ({"pack_size": None}, "pack_size", {("MISSING_CRITICAL_FIELD", "pack_size")}),
        ({"uom": None}, "uom", {("MISSING_CRITICAL_FIELD", "uom")}),
        (
            {"pack_price": None},
            "pack_price",
            {("MISSING_CRITICAL_FIELD", "unit_price"), ("MISSING_CRITICAL_FIELD", "pack_price")},
        ),
        (
            {"active_ingredients": []},
            "active_ingredients",
            {("MISSING_CRITICAL_FIELD", "active_ingredients")},
        ),
        (
            {
                "active_ingredients": [
                    ActiveIngredient(name_as_written="Amoxicillin", inn="amoxicillin")
                ]
            },
            None,
            {("MISSING_CRITICAL_FIELD", "active_ingredients")},
        ),
    ],
    ids=["pack_size", "uom", "price", "ingredients", "strength"],
)
def test_missing_critical_field_forces_review(
    write_source, store, read_model, overrides, dropped_evidence, expected
):
    spans = {k: v for k, v in LINE_EVIDENCE.items() if k != dropped_evidence}
    ingest_one(write_source, store, make_quotation(make_line(spans, **overrides)))

    [line] = read_model.review_queue()
    assert (line.review_status, reasons_of(line)) == ("needs_review", expected)


def test_missing_currency_forces_review(write_source, store, read_model):
    quotation = make_quotation(make_line(), currency=None)
    ingest_one(write_source, store, quotation)

    [line] = read_model.review_queue()
    assert (line.review_status, reasons_of(line)) == (
        "needs_review",
        {("MISSING_CRITICAL_FIELD", "currency")},
    )
    assert results_of(read_model, line)["currency"] == "failed"


def test_tier_prices_alone_satisfy_the_price_requirement(write_source, store, read_model):
    text = SOURCE.replace('"box_price": "1,250.00"', '"from 1 to 50 boxes": "1,250.00"')
    spans = {**LINE_EVIDENCE, "price_tiers": "from 1 to 50 boxes\": \"1,250.00"}
    del spans["pack_price"]
    tiers = [PriceTier(min_qty=1, max_qty=50, pack_price=1250.0)]
    ingest_one(
        write_source,
        store,
        make_quotation(make_line(spans, pack_price=None, price_tiers=tiers)),
        text,
    )

    [line] = read_model.review_queue()
    assert (line.review_status, line.review_reasons) == ("auto_ok", [])
    assert results_of(read_model, line)["price_tiers"] == "verified"


@pytest.mark.parametrize(
    ("overrides", "field"),
    [({"pack_size": 2.5}, "pack_size"), ({"discount_pct": 150.0}, "discount_pct")],
    ids=["fractional_pack_size", "discount_over_100"],
)
def test_implausible_magnitude_fails_the_field(write_source, store, read_model, overrides, field):
    ingest_one(write_source, store, make_quotation(make_line(**overrides)))

    [line] = read_model.review_queue()
    assert ("IMPLAUSIBLE_VALUE", field) in reasons_of(line)
    assert results_of(read_model, line)[field] == "failed"


def test_revision_note_forces_review_even_when_every_field_verifies(
    write_source, store, read_model
):
    line = make_line(revision_note="Pack price corrected from 1,200.00 to 1,250.00")
    ingest_one(write_source, store, make_quotation(line))

    [line] = read_model.review_queue()
    assert (line.review_status, reasons_of(line)) == ("needs_review", {("REVISION", None)})
    assert set(results_of(read_model, line).values()) == {"verified"}


def test_inferred_value_is_a_reason_without_forcing_review(write_source, store, read_model):
    line = make_line()
    line.evidence = evidence(LINE_EVIDENCE, certainty={"uom": "inferred"})
    ingest_one(write_source, store, make_quotation(line))

    [line] = read_model.review_queue()
    assert (line.review_status, reasons_of(line)) == ("auto_ok", {("INFERRED_VALUE", "uom")})
    assert results_of(read_model, line)["uom"] == "verified"


def test_unclear_critical_value_fails_and_forces_review(write_source, store, read_model):
    line = make_line()
    line.evidence = evidence(LINE_EVIDENCE, certainty={"pack_price": "unclear"})
    ingest_one(write_source, store, make_quotation(line))

    [line] = read_model.review_queue()
    assert (line.review_status, reasons_of(line)) == (
        "needs_review",
        {("UNCLEAR_VALUE", "pack_price")},
    )
