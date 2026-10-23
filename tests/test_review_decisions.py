"""Review decisions and CSV export, observed through ingest, the store's decision writes and
the read model."""

from conftest import FakeExtractor, InMemoryFxSource, make_quotation

from sdi.pipeline import ingest


def ingest_sources(write_source, store, quotations: dict):
    sources = [write_source(name, "{}") for name in quotations]
    ingest(sources, FakeExtractor(quotations), InMemoryFxSource(), store)


def test_approving_a_line_is_recorded_in_its_decision_log(write_source, store, read_model):
    ingest_sources(write_source, store, {"offer.json": make_quotation("Alpha")})
    [line] = read_model.quotation_lines()

    store.approve(line.id, reviewer="ana")

    [decision] = read_model.decisions(line.id)
    assert (decision.action, decision.reviewer, decision.field) == ("approve", "ana", None)
    assert (decision.old_value, decision.new_value) == (None, None)
    assert decision.decided_at is not None
    assert (decision.source, decision.line_no) == (line.source, 1)
    assert read_model.quotation_lines()[0].approved


def test_correction_becomes_the_current_value_and_keeps_the_extracted_one(
    write_source, store, read_model
):
    ingest_sources(write_source, store, {"offer.json": make_quotation("Alpha")})
    [line] = read_model.quotation_lines()

    store.correct(line.id, "pack_size", 20, reviewer="ana")

    [decision] = read_model.decisions(line.id)
    assert (decision.action, decision.reviewer, decision.field) == ("correct", "ana", "pack_size")
    assert (decision.old_value, decision.new_value) == (10, 20)
    [corrected] = read_model.quotation_lines()
    assert (corrected.pack_size, corrected.extracted) == (20, {"pack_size": 10})
    pack_size = next(f for f in read_model.line_detail(line.id).fields if f.field == "pack_size")
    assert (pack_size.value, pack_size.extracted_value) == (20, 10)


def test_a_second_correction_records_the_first_as_its_old_value(write_source, store, read_model):
    ingest_sources(write_source, store, {"offer.json": make_quotation("Alpha")})
    [line] = read_model.quotation_lines()

    store.correct(line.id, "pack_size", 20, reviewer="ana")
    store.correct(line.id, "pack_size", 30, reviewer="ben")

    assert [(d.old_value, d.new_value) for d in read_model.decisions(line.id)] == [
        (10, 20),
        (20, 30),
    ]
    [corrected] = read_model.quotation_lines()
    assert (corrected.pack_size, corrected.extracted) == (30, {"pack_size": 10})


def test_a_correction_after_approval_withdraws_it(write_source, store, read_model):
    ingest_sources(write_source, store, {"offer.json": make_quotation("Alpha")})
    [line] = read_model.quotation_lines()

    store.approve(line.id, reviewer="ana")
    store.correct(line.id, "uom", "tablet", reviewer="ana")

    assert not read_model.quotation_lines()[0].approved


def test_re_ingesting_keeps_decisions_and_compares_sources_on_extracted_values(
    write_source, store, read_model
):
    quotations = {"a.json": make_quotation("Alpha"), "b.json": make_quotation("Alpha")}
    quotations["b.json"].lines[0].pack_size = 20
    ingest_sources(write_source, store, quotations)
    line = next(line for line in read_model.quotation_lines() if line.source.endswith("b.json"))
    store.correct(line.id, "pack_size", 10, reviewer="ana")
    store.approve(line.id, reviewer="ana")

    ingest_sources(write_source, store, quotations)

    [line] = [line for line in read_model.quotation_lines() if line.source.endswith("b.json")]
    assert [d.action for d in read_model.decisions(line.id)] == ["correct", "approve"]
    assert (line.pack_size, line.extracted, line.approved) == (10, {"pack_size": 20}, True)
    # The Sources still disagree: a correction resolves the line, not the Conflict.
    [conflict] = read_model.conflicts()
    assert sorted(v.value for v in conflict.values) == [10, 20]


def test_prices_are_calculated_from_corrected_values(write_source, store, read_model):
    ingest_sources(write_source, store, {"offer.json": make_quotation("Alpha")})
    [line] = read_model.quotation_lines()
    assert (line.headline_unit_price, line.usd_per_uom) == (0.1, 0.1)

    store.correct(line.id, "pack_size", 20, reviewer="ana")

    [line] = read_model.quotation_lines()
    assert (line.headline_unit_price, line.usd_per_uom) == (0.05, 0.05)


def test_correcting_ingredients_changes_the_line_inns(write_source, store, read_model):
    ingest_sources(write_source, store, {"offer.json": make_quotation("Alpha")})
    [line] = read_model.quotation_lines()

    store.correct(
        line.id, "active_ingredients", [{"name_as_written": "Beta", "inn": "beta"}], reviewer="ana"
    )

    assert read_model.quotation_lines()[0].inns == ["beta"]
