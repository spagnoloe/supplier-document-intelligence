"""Calculated prices, arithmetic cross-checks and USD conversion (ADR-0002), observed through
ingest and the read model."""

from conftest import make_text_pdf
from test_review import (
    LINE_EVIDENCE,
    SOURCE,
    TIERED_EVIDENCE,
    TIERED_SOURCE,
    ingest_one,
    make_line,
    make_quotation,
    reasons_of,
    results_of,
)

from sdi.extraction.schema import PriceTier


def test_unit_price_is_calculated_from_a_stated_pack_price(write_source, store, read_model):
    ingest_one(write_source, store, make_quotation(make_line()))

    [line] = read_model.quotation_lines()
    assert (line.unit_price, line.pack_price) == (None, 1250.0)
    assert (line.headline_unit_price, line.headline_pack_price) == (12.5, 1250.0)


def test_pack_price_is_calculated_from_a_stated_unit_price(write_source, store, read_model):
    text = SOURCE.replace('"box_price": "1,250.00"', '"price_per_capsule": "12.50"')
    spans = {**LINE_EVIDENCE, "unit_price": "12.50"}
    del spans["pack_price"]
    quotation = make_quotation(make_line(spans, pack_price=None, unit_price=12.5))
    ingest_one(write_source, store, quotation, text)

    [line] = read_model.quotation_lines()
    assert (line.headline_unit_price, line.headline_pack_price) == (12.5, 1250.0)
    assert line.review_reasons == []


def test_stated_discount_gives_net_prices(write_source, store, read_model):
    text = SOURCE.replace('"Box of 100"', '"Box of 100", "discount": "10%"')
    spans = {**LINE_EVIDENCE, "discount_pct": "10%"}
    ingest_one(write_source, store, make_quotation(make_line(spans, discount_pct=10.0)), text)

    [line] = read_model.quotation_lines()
    assert (line.net_unit_price, line.net_pack_price) == (11.25, 1125.0)


def test_no_discount_leaves_net_prices_empty(write_source, store, read_model):
    ingest_one(write_source, store, make_quotation(make_line()))

    [line] = read_model.quotation_lines()
    assert (line.net_unit_price, line.net_pack_price) == (None, None)


def test_tiered_line_takes_the_lowest_volume_tier_as_headline(write_source, store, read_model):
    # Tiers listed highest volume first: the headline is the lowest-volume tier, not the first.
    text = TIERED_SOURCE.replace("12.5", "12.75")
    tiers = [
        PriceTier(min_qty=50, pack_price=11.0),
        PriceTier(min_qty=1, max_qty=49, pack_price=12.75),
    ]
    spans = {**TIERED_EVIDENCE, "price_tiers": "price_per_pack: 12.75"}
    line = make_line(spans, pack_price=None, price_tiers=tiers)
    ingest_one(write_source, store, make_quotation(line), text)

    [line] = read_model.quotation_lines()
    assert (line.headline_unit_price, line.headline_pack_price) == (0.1275, 12.75)
    assert (line.review_status, reasons_of(line)) == ("auto_ok", {("TIERED_PRICING", None)})


def ingest_scan(write_source, store, line, **kwargs):
    """A Source without text: no evidence check runs, so only arithmetic can verify a field."""
    ingest_one(
        write_source, store, make_quotation(line), make_text_pdf(), name="scan.pdf", **kwargs
    )


def test_consistent_unit_and_pack_prices_verify_each_other(write_source, store, read_model):
    # 0.42 is 12.50 / 30 rounded to the cent: within tolerance.
    ingest_scan(write_source, store, make_line(pack_size=30, unit_price=0.42, pack_price=12.5))

    [line] = read_model.quotation_lines()
    results = results_of(read_model, line)
    assert {f: results[f] for f in ("unit_price", "pack_price", "pack_size")} == {
        "unit_price": "verified",
        "pack_price": "verified",
        "pack_size": "verified",
    }
    assert results["uom"] == "unverified"


def test_inconsistent_unit_and_pack_prices_fail_with_arithmetic_mismatch(
    write_source, store, read_model
):
    ingest_scan(write_source, store, make_line(pack_size=30, unit_price=0.5, pack_price=12.5))

    [line] = read_model.quotation_lines()
    assert (line.review_status, reasons_of(line)) == (
        "needs_review",
        {
            ("ARITHMETIC_MISMATCH", "unit_price"),
            ("ARITHMETIC_MISMATCH", "pack_price"),
            ("ARITHMETIC_MISMATCH", "pack_size"),
        },
    )
    [reason, *_] = line.review_reasons
    assert reason.message == "Unit price 0.5 × Pack size 30 = 15, but Pack price is 12.5"


def test_line_total_verifies_quantity_price_and_discount(write_source, store, read_model):
    # 20 packs × 1250 less 10% = 22,500.
    line = make_line(quantity=20, discount_pct=10.0, line_total=22500.0)
    ingest_scan(write_source, store, line)

    [line] = read_model.quotation_lines()
    results = results_of(read_model, line)
    assert {f: results[f] for f in ("quantity", "line_total", "discount_pct", "pack_price")} == {
        "quantity": "verified",
        "line_total": "verified",
        "discount_pct": "verified",
        "pack_price": "verified",
    }


def test_line_total_checks_a_quantity_counted_in_uoms(write_source, store, read_model):
    # 3000 capsules × 0.42 = 1260, with 0.42 only stated per capsule.
    line = make_line(
        pack_size=30, pack_price=None, unit_price=0.42, quantity=3000, line_total=1260.0
    )
    ingest_scan(write_source, store, line)

    [line] = read_model.quotation_lines()
    results = results_of(read_model, line)
    assert (results["quantity"], results["line_total"], results["unit_price"]) == (
        "verified",
        "verified",
        "verified",
    )


def test_line_total_mismatch_fails_the_fields_involved(write_source, store, read_model):
    line = make_line(quantity=20, discount_pct=10.0, line_total=25000.0)
    ingest_scan(write_source, store, line)

    [line] = read_model.quotation_lines()
    assert (line.review_status, reasons_of(line)) == (
        "needs_review",
        {
            ("ARITHMETIC_MISMATCH", "quantity"),
            ("ARITHMETIC_MISMATCH", "line_total"),
            ("ARITHMETIC_MISMATCH", "discount_pct"),
            ("ARITHMETIC_MISMATCH", "pack_price"),
        },
    )
    [reason, *_] = line.review_reasons
    assert reason.message == (
        "Quantity 20 × Pack price 1250 less 10% = 22500, but line total is 25000"
    )
