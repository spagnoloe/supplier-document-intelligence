"""Calculated prices, arithmetic cross-checks and USD conversion (ADR-0002), observed through
ingest and the read model."""

from test_review import LINE_EVIDENCE, SOURCE, ingest_one, make_line, make_quotation


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
