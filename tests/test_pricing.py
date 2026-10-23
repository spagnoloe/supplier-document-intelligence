"""Calculated prices, arithmetic cross-checks and USD conversion (ADR-0002), observed through
ingest and the read model."""

from test_review import (
    LINE_EVIDENCE,
    SOURCE,
    TIERED_EVIDENCE,
    TIERED_SOURCE,
    ingest_one,
    make_line,
    make_quotation,
    reasons_of,
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
