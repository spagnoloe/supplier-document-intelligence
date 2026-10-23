import json

from conftest import FakeExtractor, InMemoryFxSource, make_quotation

from sdi.pipeline import ingest


def test_json_source_lines_are_readable_through_read_model(write_source, store, read_model):
    source = write_source(
        "export.json", json.dumps({"items": [{"name": "Alpha"}, {"name": "Beta"}]})
    )
    extractor = FakeExtractor({"export.json": make_quotation("Alpha", "Beta")})

    ingest([source], extractor, InMemoryFxSource(), store)

    [received] = extractor.received
    assert '"name": "Beta"' in received.text
    [status] = read_model.document_statuses()
    assert (status.source, status.status, status.line_count) == (str(source), "processed", 2)
    lines = read_model.quotation_lines()
    assert [line.inns for line in lines] == [["alpha"], ["beta"]]
    assert {line.supplier for line in lines} == {"Synthetic Supplier Ltd"}


def test_failing_source_is_recorded_and_others_still_process(write_source, store, read_model):
    good = write_source("good.json", "{}")
    bad = write_source("bad.json", "{}")
    extractor = FakeExtractor(
        {"good.json": make_quotation("Alpha"), "bad.json": RuntimeError("model unavailable")}
    )

    ingest([bad, good], extractor, InMemoryFxSource(), store)

    statuses = {s.source: s for s in read_model.document_statuses()}
    assert statuses[str(good)].status == "processed"
    assert statuses[str(bad)].status == "failed"
    assert "model unavailable" in statuses[str(bad)].error


def test_malformed_json_source_is_recorded_as_failed(write_source, store, read_model):
    broken = write_source("broken.json", "{not json")

    ingest([broken], FakeExtractor({}), InMemoryFxSource(), store)

    [status] = read_model.document_statuses()
    assert (status.status, status.file_type) == ("failed", "json")


def test_source_with_unregistered_file_type_is_failed_with_no_loader(
    write_source, store, read_model
):
    source = write_source("notes.xyz", "anything")
    extractor = FakeExtractor({})

    ingest([source], extractor, InMemoryFxSource(), store)

    [status] = read_model.document_statuses()
    assert (status.status, status.error) == ("failed", "no loader for .xyz")
    assert extractor.received == []


def test_non_quotation_document_is_skipped_as_unsupported_type(write_source, store, read_model):
    source = write_source("confirmation.json", "{}")
    extractor = FakeExtractor(
        {"confirmation.json": make_quotation("Alpha", document_type="order_confirmation")}
    )

    ingest([source], extractor, InMemoryFxSource(), store)

    [status] = read_model.document_statuses()
    assert (status.status, status.document_type, status.line_count) == (
        "skipped_unsupported_type",
        "order_confirmation",
        0,
    )
    assert read_model.quotation_lines() == []
