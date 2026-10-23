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
