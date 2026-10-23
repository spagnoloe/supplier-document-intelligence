import json
from datetime import date

from conftest import (
    FakeExtractor,
    InMemoryFxSource,
    make_email,
    make_quotation,
    make_text_pdf,
    make_workbook,
)
from langchain_core.exceptions import OutputParserException

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


def test_structured_output_failure_is_retried_then_processed(write_source, store, read_model):
    source = write_source("flaky.json", "{}")
    extractor = FakeExtractor(
        {"flaky.json": [OutputParserException("invalid JSON"), make_quotation("Alpha")]}
    )

    ingest([source], extractor, InMemoryFxSource(), store)

    [status] = read_model.document_statuses()
    assert (status.status, status.line_count) == ("processed", 1)
    assert extractor.calls_for("flaky.json") == 2


def test_structured_output_failure_gives_up_after_bounded_attempts(write_source, store, read_model):
    source = write_source("hopeless.json", "{}")
    extractor = FakeExtractor({"hopeless.json": OutputParserException("invalid JSON")})

    ingest([source], extractor, InMemoryFxSource(), store, max_attempts=3)

    [status] = read_model.document_statuses()
    assert status.status == "failed"
    assert "invalid JSON" in status.error
    assert extractor.calls_for("hopeless.json") == 3


def test_text_pdf_reaches_extractor_as_its_text(write_source, store, read_model):
    source = write_source(
        "offer.pdf", make_text_pdf("Quotation Q-1", "Alpha 500 mg tablets (box of 10) 1.00")
    )
    extractor = FakeExtractor({"offer.pdf": make_quotation("Alpha")})

    ingest([source], extractor, InMemoryFxSource(), store)

    [received] = extractor.received
    assert "Quotation Q-1" in received.text
    assert "Alpha 500 mg tablets (box of 10) 1.00" in received.text
    assert received.images == []
    [status] = read_model.document_statuses()
    assert (status.file_type, status.status, status.line_count) == ("pdf", "processed", 1)


def test_multipart_email_reaches_extractor_as_headers_and_plain_body_once(
    write_source, store, read_model
):
    source = write_source(
        "reply.eml",
        make_email(
            plain="Alpha 500 mg: 0.12 per tablet\n",
            html="<p>Alpha 500 mg: <b>0.12</b> per tablet</p>",
        ),
    )
    extractor = FakeExtractor({"reply.eml": make_quotation("Alpha")})

    ingest([source], extractor, InMemoryFxSource(), store)

    [received] = extractor.received
    assert "From: Sales <sales@synthetic.example>" in received.text
    assert "Date: Tue, 04 Aug 2026 16:42:11 +0200" in received.text
    assert "Subject: RE: RFQ-1 quotation" in received.text
    assert received.text.count("Alpha 500 mg") == 1
    assert "Alpha 500 mg: 0.12 per tablet" in received.text
    assert "<b>" not in received.text
    [status] = read_model.document_statuses()
    assert (status.file_type, status.status) == ("eml", "processed")


def test_html_only_email_reaches_extractor_as_text_without_markup(write_source, store, read_model):
    html = (
        "<html><head><style>p {color: red}</style></head><body>"
        "<p>Prices in EUR &ndash; FCA</p>"
        "<table><tr><th>Product</th><th>Price</th></tr>"
        "<tr><td>Alpha 500 mg</td><td>0.12 per tablet</td></tr>"
        "<tr><td>Beta 20 mg</td><td>1.55 per box</td></tr></table>"
        "<p>Regards,<br>Sales</p></body></html>"
    )
    source = write_source("offer.eml", make_email(plain=None, html=html))
    extractor = FakeExtractor({"offer.eml": make_quotation("Alpha", "Beta")})

    ingest([source], extractor, InMemoryFxSource(), store)

    [received] = extractor.received
    assert "Subject: RE: RFQ-1 quotation" in received.text
    assert "Prices in EUR – FCA" in received.text
    # Table rows stay on their own lines, so a price can't drift onto another product.
    lines = received.text.splitlines()
    assert any("Alpha 500 mg" in line and "0.12 per tablet" in line for line in lines)
    assert any("Beta 20 mg" in line and "1.55 per box" in line for line in lines)
    assert "Regards,\nSales" in received.text
    assert "<" not in received.text.split("\n\n", 1)[1]
    assert "color: red" not in received.text


def test_pdf_without_text_layer_reaches_extractor_with_no_text(write_source, store):
    source = write_source("scan.pdf", make_text_pdf())
    extractor = FakeExtractor({"scan.pdf": make_quotation("Alpha")})

    ingest([source], extractor, InMemoryFxSource(), store)

    [received] = extractor.received
    assert received.text is None


def test_multi_sheet_workbook_reaches_extractor_as_text_one_row_per_line(
    write_source, store, read_model
):
    source = write_source(
        "prices.xlsx",
        make_workbook(
            {
                "Prices": [
                    ["Price list PL-1", None, None],
                    [],
                    ["Product", "Pack", "Unit price"],
                    ["Alpha 500 mg tablet", 20, 0.0091],
                    ["Beta 20 mg tablet", 30, 1.55],
                ],
                "Terms": [["Currency", "USD"], ["Valid until", date(2026, 9, 20)]],
            }
        ),
    )
    extractor = FakeExtractor({"prices.xlsx": make_quotation("Alpha", "Beta")})

    ingest([source], extractor, InMemoryFxSource(), store)

    [received] = extractor.received
    # Each row stays on one line, so a price can't drift onto another product.
    lines = received.text.splitlines()
    assert "Product | Pack | Unit price" in lines
    assert "Alpha 500 mg tablet | 20 | 0.0091" in lines
    assert "Beta 20 mg tablet | 30 | 1.55" in lines
    # Every sheet is kept and labelled, so terms on a second sheet still reach the model.
    assert received.text.index("--- sheet Prices ---") < received.text.index("Beta 20 mg")
    assert received.text.index("--- sheet Terms ---") < received.text.index("Currency | USD")
    assert "Valid until | 2026-09-20" in lines
    assert received.images == []
    [status] = read_model.document_statuses()
    assert (status.file_type, status.status, status.line_count) == ("xlsx", "processed", 2)


def test_workbook_without_cell_values_reaches_extractor_with_no_text(write_source, store):
    source = write_source("blank.xlsx", make_workbook({"Sheet1": [], "Sheet2": [[None, None]]}))
    extractor = FakeExtractor({"blank.xlsx": make_quotation("Alpha")})

    ingest([source], extractor, InMemoryFxSource(), store)

    [received] = extractor.received
    assert received.text is None
