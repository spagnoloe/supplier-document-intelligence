"""Verification and review status (ADR-0004), observed through ingest and the read model."""

import json

import pytest
from conftest import FakeExtractor, InMemoryFxSource

from sdi.cli import format_summary
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


def make_quotation(
    *lines: QuotationLine, currency: str | None = "USD", supplier: str = "Synthetic Pharma Ltd"
) -> Quotation:
    return Quotation(
        document_type="quotation",
        supplier=supplier,
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
    spans = {**LINE_EVIDENCE, "price_tiers": 'from 1 to 50 boxes": "1,250.00'}
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


def test_review_queue_lists_needs_review_first_and_filters(write_source, store, read_model):
    clean, flagged = write_source("a.json", SOURCE), write_source("b.json", SOURCE)
    wrong_price = make_line({**LINE_EVIDENCE, "pack_price": "1,350.00"})
    extractor = FakeExtractor(
        {
            "a.json": make_quotation(make_line(), make_line(), supplier="Alpha Ltd"),
            "b.json": make_quotation(make_line(), wrong_price, supplier="Beta Ltd"),
        }
    )
    ingest([clean, flagged], extractor, InMemoryFxSource(), store)

    queue = read_model.review_queue()
    assert [(q.review_status, q.supplier, q.line_no) for q in queue] == [
        ("needs_review", "Beta Ltd", 2),
        ("auto_ok", "Alpha Ltd", 1),
        ("auto_ok", "Alpha Ltd", 2),
        ("auto_ok", "Beta Ltd", 1),
    ]

    def keys(lines):
        return [(q.supplier, q.line_no) for q in lines]

    assert keys(read_model.review_queue(supplier="Beta Ltd")) == [("Beta Ltd", 2), ("Beta Ltd", 1)]
    assert keys(read_model.review_queue(source=str(clean))) == [("Alpha Ltd", 1), ("Alpha Ltd", 2)]
    assert keys(read_model.review_queue(review_status="needs_review")) == [("Beta Ltd", 2)]
    assert keys(read_model.review_queue(reason_code="EVIDENCE_NOT_FOUND")) == [("Beta Ltd", 2)]
    assert read_model.review_queue(supplier="Alpha Ltd", review_status="needs_review") == []


def test_line_detail_attaches_reasons_to_their_field(write_source, store, read_model):
    line = make_line({**LINE_EVIDENCE, "pack_price": "1,350.00"}, revision_note="Price revised")
    ingest_one(write_source, store, make_quotation(line))

    [line] = read_model.review_queue()
    detail = read_model.line_detail(line.id)
    pack_price = next(f for f in detail.fields if f.field == "pack_price")
    assert [r.code for r in pack_price.reasons] == ["EVIDENCE_NOT_FOUND"]
    assert {r.code for r in detail.line.review_reasons} == {"EVIDENCE_NOT_FOUND", "REVISION"}
    currency = next(f for f in detail.fields if f.field == "currency")
    assert (currency.value, [e.span for e in currency.evidence]) == ("USD", ["USD"])


def test_cli_summary_shows_lines_needing_review_per_source(write_source, store, read_model):
    wrong_price = make_line({**LINE_EVIDENCE, "pack_price": "1,350.00"})
    ingest_one(write_source, store, make_quotation(make_line(), wrong_price, make_line()))

    header, row = format_summary(read_model.document_statuses()).splitlines()
    assert header.split()[:4] == ["STATUS", "LINES", "REVIEW", "SOURCE"]
    assert row.split()[:4] == ["processed", "3", "1", "offer.json"]


TIERED_SOURCE = json.dumps(
    {
        "currency": "USD",
        "items": [
            {
                "ingredient": "Amoxicillin",
                "strength": "500 mg",
                "form": "hard capsules",
                "pack": "Box of 100",
                "tiers": [
                    {"min_packs": 1, "max_packs": 49, "price_per_pack": 12.5},
                    {"min_packs": 50, "max_packs": None, "price_per_pack": 11.0},
                ],
            }
        ],
    }
)

TIERED_EVIDENCE = {
    "active_ingredients": "Amoxicillin",
    "dosage_form": "hard capsules",
    "uom": "capsules",
    "pack_size": "Box of 100",
    # The model quotes one tier, and drops the JSON quoting.
    "price_tiers": "min_packs: 1, max_packs: 49, price_per_pack: 12.5",
}


def tiered_line(second_tier_min_qty: float = 50) -> QuotationLine:
    tiers = [
        PriceTier(min_qty=1, max_qty=49, pack_price=12.5),
        PriceTier(min_qty=second_tier_min_qty, pack_price=11.0),
    ]
    return make_line(TIERED_EVIDENCE, pack_price=None, price_tiers=tiers)


def test_list_entries_verify_against_numbers_anywhere_in_the_source(
    write_source, store, read_model
):
    ingest_one(write_source, store, make_quotation(tiered_line()), TIERED_SOURCE)

    [line] = read_model.review_queue()
    assert (line.review_status, line.review_reasons) == ("auto_ok", [])
    results = results_of(read_model, line)
    assert (results["active_ingredients"], results["price_tiers"]) == ("verified", "verified")


def test_list_entry_number_absent_from_the_source_fails_evidence(write_source, store, read_model):
    # 50 packs converted to 5000 capsules: a value the document never states.
    quotation = make_quotation(tiered_line(second_tier_min_qty=5000))
    ingest_one(write_source, store, quotation, TIERED_SOURCE)

    [line] = read_model.review_queue()
    assert (line.review_status, reasons_of(line)) == (
        "needs_review",
        {("EVIDENCE_NOT_FOUND", "price_tiers")},
    )
    [reason] = line.review_reasons
    assert reason.message == "5000 not found in source text"


@pytest.mark.parametrize("code", ["usd", "XXX", "XAU"], ids=["lower_case", "no_currency", "gold"])
def test_lower_case_and_non_money_iso_codes_are_invalid_currencies(
    write_source, store, read_model, code
):
    text = SOURCE.replace('"USD"', f'"{code}"')
    ingest_one(write_source, store, make_quotation(make_line(), currency=code), text)

    [line] = read_model.review_queue()
    assert reasons_of(line) == {("INVALID_CURRENCY", "currency")}


def test_full_discount_is_plausible(write_source, store, read_model):
    text = SOURCE.replace('"Box of 100"', '"Box of 100", "discount": "100%"')
    spans = {**LINE_EVIDENCE, "discount_pct": "100%"}
    ingest_one(write_source, store, make_quotation(make_line(spans, discount_pct=100.0)), text)

    [line] = read_model.review_queue()
    assert line.review_reasons == []
