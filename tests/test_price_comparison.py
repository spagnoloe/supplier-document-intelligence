"""Price comparison (ADR-0003): lines for one Product from different Quotations set side by
side in USD per UOM, observed through ingest and the read model."""

import json
from datetime import date

import pytest
from conftest import FakeExtractor, InMemoryFxSource

from sdi.extraction.schema import (
    ActiveIngredient,
    LineFieldEvidence,
    Quotation,
    QuotationFieldEvidence,
    QuotationLine,
)
from sdi.fx import FxRate
from sdi.pipeline import ingest

ISSUED = date(2026, 8, 3)

SOURCE = json.dumps(
    {
        "supplier": "Synthetic Pharma Ltd",
        "ref": "SP-77",
        "currency": "USD",
        "items": [
            {"description": "Amoxicillin trihydrate 500 mg capsules", "pack": "Box of 100"},
            {"description": "Amoxicillin 500 mg capsules", "pack": "Box of 20"},
            {"price": "12.50 / 4.00 / 0.20", "incoterm": "FCA EXW CIF"},
        ],
    }
)


def amoxicillin(
    pack_size: float = 100,
    pack_price: float | None = 12.5,
    unit_price: float | None = None,
    name: str = "Amoxicillin",
    inn: str = "amoxicillin",
    dosage_form: str = "Capsule",
) -> QuotationLine:
    spans = {
        "active_ingredients": f"{name} 500 mg",
        "dosage_form": "capsules",
        "uom": "capsules",
        "pack_size": f"Box of {pack_size:g}",
        "pack_price": None if pack_price is None else f"{pack_price:.2f}",
        "unit_price": None if unit_price is None else f"{unit_price:.2f}",
    }
    return QuotationLine(
        active_ingredients=[
            ActiveIngredient(name_as_written=name, inn=inn, strength_value=500, strength_unit="mg")
        ],
        dosage_form=dosage_form,
        uom="capsule",
        pack_size=pack_size,
        pack_price=pack_price,
        unit_price=unit_price,
        evidence=[
            LineFieldEvidence(field=f, span=s, certainty="stated")
            for f, s in spans.items()
            if s is not None
        ],
    )


def quotation(
    *lines: QuotationLine,
    supplier: str = "Synthetic Pharma Ltd",
    quote_ref: str = "SP-77",
    currency: str = "USD",
    incoterm: str | None = "FCA",
    incoterm_place: str | None = None,
) -> Quotation:
    return Quotation(
        document_type="quotation",
        supplier=supplier,
        quote_ref=quote_ref,
        issue_date=ISSUED,
        currency=currency,
        incoterm=incoterm,
        incoterm_place=incoterm_place,
        lines=list(lines),
        evidence=[QuotationFieldEvidence(field="currency", span=currency, certainty="stated")],
    )


def ingest_sources(write_source, store, quotations: dict[str, Quotation], fx=None) -> None:
    sources = [write_source(name, SOURCE) for name in quotations]
    ingest(sources, FakeExtractor(quotations), fx or InMemoryFxSource(), store)


def test_lines_for_one_product_from_different_quotations_are_grouped_across_salt_names(
    write_source, store, read_model
):
    ingest_sources(
        write_source,
        store,
        {
            # The INN should come without the salt, but a model sometimes leaves it in.
            "a.json": quotation(
                amoxicillin(name="Amoxicillin trihydrate", inn="Amoxicillin Trihydrate")
            ),
            "b.json": quotation(amoxicillin(), supplier="Other Pharma", quote_ref="OP-1"),
        },
    )

    [group] = read_model.price_comparisons()
    # Each line keeps its INN as extracted; only the grouping looks past the salt.
    assert [line.inns for line in group.lines] == [["Amoxicillin Trihydrate"], ["amoxicillin"]]
    assert group.dosage_form == "Capsule"
    assert [(line.supplier, line.quote_ref) for line in group.lines] == [
        ("Synthetic Pharma Ltd", "SP-77"),
        ("Other Pharma", "OP-1"),
    ]


def test_different_dosage_forms_of_one_ingredient_are_never_grouped(
    write_source, store, read_model
):
    ingest_sources(
        write_source,
        store,
        {
            "a.json": quotation(amoxicillin()),
            "b.json": quotation(
                amoxicillin(dosage_form="Powder for oral suspension"),
                supplier="Other Pharma",
                quote_ref="OP-1",
            ),
        },
    )

    assert read_model.price_comparisons() == []


def test_sources_of_one_quotation_alone_are_not_a_price_comparison(write_source, store, read_model):
    ingest_sources(
        write_source,
        store,
        {"a.json": quotation(amoxicillin()), "b.json": quotation(amoxicillin())},
    )

    assert read_model.price_comparisons() == []


def test_lines_with_an_unreadable_strength_or_form_are_not_compared(
    write_source, store, read_model
):
    def unreadable_strength() -> QuotationLine:
        line = amoxicillin()
        line.active_ingredients[0].strength_value = None
        return line

    def unreadable_form() -> QuotationLine:
        line = amoxicillin()
        line.dosage_form = None
        return line

    ingest_sources(
        write_source,
        store,
        {
            "a.json": quotation(unreadable_strength(), unreadable_form()),
            "b.json": quotation(
                unreadable_strength(), unreadable_form(), supplier="Other Pharma", quote_ref="OP-1"
            ),
        },
    )

    assert read_model.price_comparisons() == []


def test_prices_are_compared_in_usd_per_uom_before_discount_whatever_the_currency_and_pack(
    write_source, store, read_model
):
    per_box_in_eur = amoxicillin(pack_size=100, pack_price=12.5)
    per_box_in_eur.discount_pct = 10
    per_capsule_in_usd = amoxicillin(pack_size=20, pack_price=None, unit_price=0.2)
    fx = InMemoryFxSource(
        {("EUR", ISSUED): FxRate(rate=1.2, rate_date=ISSUED, source="Synthetic rates")}
    )
    ingest_sources(
        write_source,
        store,
        {
            "a.json": quotation(per_box_in_eur, currency="EUR"),
            "b.json": quotation(per_capsule_in_usd, supplier="Other Pharma", quote_ref="OP-1"),
        },
        fx,
    )

    [group] = read_model.price_comparisons()
    # 12.50 EUR per box of 100 is 0.125 EUR, at 1.2 USD per EUR 0.15 USD per capsule.
    assert [(line.currency, line.usd_per_uom) for line in group.lines] == [
        ("EUR", pytest.approx(0.15)),
        ("USD", pytest.approx(0.2)),
    ]


def test_group_with_mixed_incoterms_warns_its_prices_are_not_like_for_like(
    write_source, store, read_model
):
    ingest_sources(
        write_source,
        store,
        {
            "a.json": quotation(amoxicillin(), incoterm="FCA", incoterm_place="Mumbai"),
            "b.json": quotation(
                amoxicillin(), supplier="Other Pharma", quote_ref="OP-1", incoterm="CIF"
            ),
        },
    )

    [group] = read_model.price_comparisons()
    assert "not like-for-like" in group.incoterm_warning
    assert [(line.incoterm, line.incoterm_place) for line in group.lines] == [
        ("FCA", "Mumbai"),
        ("CIF", None),
    ]


def test_group_under_one_incoterm_has_no_warning(write_source, store, read_model):
    ingest_sources(
        write_source,
        store,
        {
            "a.json": quotation(amoxicillin(), incoterm="FCA", incoterm_place="Mumbai"),
            "b.json": quotation(
                amoxicillin(), supplier="Other Pharma", quote_ref="OP-1", incoterm="fca"
            ),
        },
    )

    [group] = read_model.price_comparisons()
    assert group.incoterm_warning is None


def test_quotation_stating_no_incoterm_makes_its_group_not_like_for_like(
    write_source, store, read_model
):
    ingest_sources(
        write_source,
        store,
        {
            "a.json": quotation(amoxicillin(), incoterm="FCA"),
            "b.json": quotation(
                amoxicillin(), supplier="Other Pharma", quote_ref="OP-1", incoterm=None
            ),
        },
    )

    [group] = read_model.price_comparisons()
    assert "not like-for-like" in group.incoterm_warning


def test_price_comparison_leaves_every_review_status_as_verification_set_it(
    write_source, store, read_model
):
    ingest_sources(
        write_source,
        store,
        {
            "a.json": quotation(amoxicillin(pack_size=100, pack_price=12.5)),
            "b.json": quotation(
                amoxicillin(pack_size=20, pack_price=4.0),
                supplier="Other Pharma",
                quote_ref="OP-1",
                incoterm="CIF",
            ),
        },
    )

    [group] = read_model.price_comparisons()
    assert group.incoterm_warning is not None
    assert [(line.review_status, line.review_reasons) for line in group.lines] == [
        ("auto_ok", []),
        ("auto_ok", []),
    ]


def test_rerun_replaces_earlier_price_comparisons(write_source, store, read_model):
    a, b = write_source("a.json", SOURCE), write_source("b.json", SOURCE)
    other = {"supplier": "Other Pharma", "quote_ref": "OP-1"}
    quotations = {"a.json": quotation(amoxicillin()), "b.json": quotation(amoxicillin(), **other)}
    ingest([a, b], FakeExtractor(quotations), InMemoryFxSource(), store)
    ingest([a, b], FakeExtractor(quotations), InMemoryFxSource(), store)
    assert len(read_model.price_comparisons()) == 1

    syrup = amoxicillin(dosage_form="Syrup")
    ingest([b], FakeExtractor({"b.json": quotation(syrup, **other)}), InMemoryFxSource(), store)

    assert read_model.price_comparisons() == []


def test_inorganic_salts_of_one_cation_are_different_products(write_source, store, read_model):
    def salt(inn: str) -> QuotationLine:
        return amoxicillin(name=inn, inn=inn, dosage_form="Solution for injection")

    ingest_sources(
        write_source,
        store,
        {
            "a.json": quotation(salt("magnesium sulfate")),
            "b.json": quotation(
                salt("magnesium chloride"), supplier="Other Pharma", quote_ref="OP-1"
            ),
        },
    )

    assert read_model.price_comparisons() == []
