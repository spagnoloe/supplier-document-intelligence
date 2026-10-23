"""Review decisions and CSV export, observed through ingest, the store's decision writes and
the read model."""

import csv
from io import StringIO

import pytest
from conftest import FakeExtractor, InMemoryFxSource, make_quotation

from sdi.cli import main
from sdi.export import export_csv
from sdi.pipeline import ingest


def ingest_sources(write_source, store, quotations: dict, extraction_version: str = ""):
    sources = [write_source(name, "{}") for name in quotations]
    ingest(
        sources,
        FakeExtractor(quotations),
        InMemoryFxSource(),
        store,
        extraction_version=extraction_version,
    )


def test_approving_a_line_is_recorded_in_its_decision_log(write_source, store, read_model):
    ingest_sources(write_source, store, {"offer.json": make_quotation("Alpha")})
    [line] = read_model.quotation_lines()

    store.approve(line.id, reviewer="ana")

    [decision] = read_model.decisions(line.id)
    assert (decision.action, decision.reviewer, decision.field) == ("approve", "ana", None)
    assert (decision.old_value, decision.new_value) == (None, None)
    assert decision.decided_at is not None
    assert (decision.source, decision.line_no, decision.current) == (line.source, 1, True)
    assert read_model.quotation_lines()[0].review_status == "reviewed"


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
    assert (corrected.pack_size, corrected.extracted_values) == (20, {"pack_size": 10})
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
    assert (corrected.pack_size, corrected.extracted_values) == (30, {"pack_size": 10})


def test_a_correction_after_approval_withdraws_it(write_source, store, read_model):
    ingest_sources(write_source, store, {"offer.json": make_quotation("Alpha")})
    [line] = read_model.quotation_lines()

    store.approve(line.id, reviewer="ana")
    store.correct(line.id, "uom", "tablet", reviewer="ana")

    assert read_model.quotation_lines()[0].review_status == line.review_status != "reviewed"


def test_re_ingesting_unchanged_sources_keeps_decisions_and_compares_extracted_values(
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
    assert (line.pack_size, line.extracted_values, line.review_status) == (
        10,
        {"pack_size": 20},
        "reviewed",
    )
    # The Sources still disagree: a correction resolves the line, not the Conflict.
    [conflict] = read_model.conflicts()
    assert sorted(v.value for v in conflict.values) == [10, 20]


def test_reading_a_source_again_starts_its_review_afresh_and_keeps_the_log(
    write_source, store, read_model
):
    ingest_sources(write_source, store, {"offer.json": make_quotation("Alpha")}, "v1")
    [line] = read_model.quotation_lines()
    store.correct(line.id, "pack_size", 20, reviewer="ana")
    store.approve(line.id, reviewer="ana")

    ingest_sources(write_source, store, {"offer.json": make_quotation("Alpha")}, "v2")

    [line] = read_model.quotation_lines()
    assert (line.pack_size, line.extracted_values, line.review_status) == (10, {}, "needs_review")
    assert read_model.decisions(line.id) == []
    log = read_model.decision_log()
    assert [(d.action, d.source, d.line_no, d.current) for d in log] == [
        ("correct", line.source, 1, False),
        ("approve", line.source, 1, False),
    ]


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


def test_export_has_current_values_review_status_reasons_and_source(
    write_source, store, read_model
):
    quotation = make_quotation("Alpha", "Beta")
    quotation.lines[1].pack_size = 0
    ingest_sources(write_source, store, {"offer.json": quotation})
    alpha, beta = read_model.quotation_lines()
    store.correct(alpha.id, "pack_size", 20, reviewer="ana")
    store.approve(alpha.id, reviewer="ana")

    out = StringIO()
    export_csv(read_model, out)

    rows = list(csv.DictReader(StringIO(out.getvalue())))
    assert [(r["source"], r["line_no"]) for r in rows] == [(alpha.source, "1"), (beta.source, "2")]
    assert rows[0]["active_ingredients"] == "alpha"
    assert (rows[0]["pack_size"], rows[0]["headline_unit_price"]) == ("20", "0.05")
    assert (rows[0]["review_status"], rows[0]["corrected_fields"]) == ("reviewed", "pack_size")
    assert rows[1]["review_status"] == "needs_review"
    assert "IMPLAUSIBLE_VALUE" in rows[1]["review_reasons"]


def test_cli_writes_the_export(write_source, db_path, store, tmp_path, monkeypatch):
    ingest_sources(write_source, store, {"offer.json": make_quotation("Alpha")})
    out = tmp_path / "lines.csv"
    monkeypatch.setenv("SDI_DB_PATH", str(db_path))

    main(["export", str(out)])

    [row] = csv.DictReader(out.open())
    assert row["active_ingredients"] == "alpha"


def test_clearing_a_line_currency_falls_back_to_the_quotation_currency(
    write_source, store, read_model
):
    quotation = make_quotation("Alpha")
    quotation.lines[0].currency = "EUR"
    ingest_sources(write_source, store, {"offer.json": quotation})
    [line] = read_model.quotation_lines()

    store.correct(line.id, "currency", None, reviewer="ana")

    [line] = read_model.quotation_lines()
    assert (line.currency, line.usd_per_uom) == ("USD", 0.1)


def test_a_corrected_currency_with_no_rate_says_so(write_source, store, read_model):
    ingest_sources(write_source, store, {"offer.json": make_quotation("Alpha")})
    [line] = read_model.quotation_lines()

    store.correct(line.id, "currency", "EUR", reviewer="ana")

    [line] = read_model.quotation_lines()
    assert line.usd_per_uom is None
    assert "FX_UNAVAILABLE" in {r.code for r in line.review_reasons}


@pytest.mark.parametrize("reviewer", ["", "  "])
def test_a_decision_needs_a_reviewer(write_source, store, read_model, reviewer):
    ingest_sources(write_source, store, {"offer.json": make_quotation("Alpha")})
    [line] = read_model.quotation_lines()

    with pytest.raises(ValueError):
        store.approve(line.id, reviewer=reviewer)
    with pytest.raises(ValueError):
        store.correct(line.id, "uom", "tablet", reviewer=reviewer)
    assert read_model.decisions(line.id) == []


def test_reviewed_lines_leave_the_needs_review_queue_and_count(write_source, store, read_model):
    quotation = make_quotation("Alpha", "Beta")
    for line in quotation.lines:
        line.pack_size = 0
    ingest_sources(write_source, store, {"offer.json": quotation})
    alpha, beta = read_model.quotation_lines()

    store.correct(alpha.id, "pack_size", 10, reviewer="ana")
    store.approve(alpha.id, reviewer="ana")

    assert [line.id for line in read_model.review_queue()] == [beta.id, alpha.id]
    assert [line.id for line in read_model.review_queue(review_status="needs_review")] == [beta.id]
    assert [line.id for line in read_model.review_queue(review_status="reviewed")] == [alpha.id]
    [status] = read_model.document_statuses()
    assert (status.line_count, status.needs_review_count) == (2, 1)
    # The reasons it was flagged for stay visible on the reviewed line.
    assert "IMPLAUSIBLE_VALUE" in {r.code for r in read_model.review_queue()[1].review_reasons}


def test_a_new_reason_after_approval_reopens_the_line(write_source, store, read_model):
    ingest_sources(write_source, store, {"a.json": make_quotation("Alpha")})
    [line] = read_model.quotation_lines()
    store.approve(line.id, reviewer="ana")

    # A second Source of the same Quotation arrives and disagrees with the approved line.
    disagreeing = make_quotation("Alpha")
    disagreeing.lines[0].pack_size = 20
    ingest_sources(write_source, store, {"b.json": disagreeing})

    line = next(line for line in read_model.quotation_lines() if line.source.endswith("a.json"))
    assert line.review_status == "needs_review"
    assert "IN_CONFLICT" in {r.code for r in line.review_reasons}
    store.approve(line.id, reviewer="ana")
    assert read_model.line_detail(line.id).line.review_status == "reviewed"
