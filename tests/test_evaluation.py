"""Eval scoring: a database built by ingest, scored against a hand-labelled gold set."""

import json

from conftest import FakeExtractor, InMemoryFxSource

from sdi.evaluation import GoldSource, score
from sdi.extraction.schema import (
    ActiveIngredient,
    LineFieldEvidence,
    Quotation,
    QuotationFieldEvidence,
    QuotationLine,
)
from sdi.pipeline import ingest

SOURCE_TEXT = json.dumps(
    {"currency": "USD", "offer": "Paracetamol 500 mg tablet, 20 per pack, 0.0091 per tablet"}
)

# Spans found in SOURCE_TEXT, so these fields verify unless a test changes a value or span.
SPANS = {
    "active_ingredients": "Paracetamol 500 mg",
    "uom": "tablet",
    "pack_size": "20 per pack",
    "unit_price": "0.0091",
}


def line(inn: str, spans: dict[str, str] | None = None, **values) -> QuotationLine:
    fields = {
        "active_ingredients": [
            ActiveIngredient(name_as_written=inn, inn=inn, strength_value=500, strength_unit="mg")
        ],
        "uom": "tablet",
        "pack_size": 20,
        "unit_price": 0.0091,
        "evidence": [
            LineFieldEvidence(field=f, span=s, certainty="stated")
            for f, s in {**SPANS, **(spans or {})}.items()
        ],
    }
    return QuotationLine(**{**fields, **values})


def ingest_lines(write_source, store, *lines: QuotationLine, name: str = "offer.json"):
    source = write_source(name, SOURCE_TEXT)
    quotation = Quotation(
        document_type="quotation",
        supplier="Synthetic Pharma Ltd",
        quote_ref="Q-1",
        currency="USD",
        lines=list(lines),
        evidence=[QuotationFieldEvidence(field="currency", span="USD", certainty="stated")],
    )
    ingest([source], FakeExtractor({name: quotation}), InMemoryFxSource(), store)


def gold(*lines: dict, name: str = "offer.json", source_type: str = "json") -> GoldSource:
    return GoldSource(
        source=name,
        source_type=source_type,
        quotation={"supplier": "Synthetic Pharma Ltd", "quote_ref": "Q-1", "currency": "USD"},
        lines=list(lines),
    )


def gold_line(inn: str, **values) -> dict:
    return {
        "active_ingredients": [{"inn": inn, "strength_value": 500, "strength_unit": "mg"}],
        **values,
    }


def test_reports_expected_lines_found_missed_and_extra_lines(write_source, store, read_model):
    ingest_lines(write_source, store, line("paracetamol"), line("aspirin"))

    report = score([gold(gold_line("paracetamol"), gold_line("ibuprofen"))], read_model)

    matching = report.by_type["json"].lines
    assert (matching.expected, matching.found, matching.missed, matching.extra) == (2, 1, 1, 1)
    assert report.overall.lines == matching


def accuracy(fields) -> dict[str, tuple[int, int]]:
    return {field: (a.correct, a.scored) for field, a in fields.items()}


def test_scores_matched_fields_with_number_tolerance_and_normalized_strings(
    write_source, store, read_model
):
    ingest_lines(write_source, store, line("paracetamol", dosage_form="Tablet"))
    expected = gold_line(
        "paracetamol",
        uom="Tablet.",  # normalized: case and punctuation don't count
        pack_size=20,
        unit_price=0.009136,  # 0.4% off the extracted 0.0091: within tolerance
        dosage_form="Film-coated tablet",
        discount_pct=None,  # expected empty, and empty it is
        line_total=52962.0,  # expected but not extracted
    )

    report = score([gold(expected)], read_model)

    assert accuracy(report.overall.fields) == {
        "active_ingredients": (1, 1),
        "uom": (1, 1),
        "pack_size": (1, 1),
        "unit_price": (1, 1),
        "dosage_form": (0, 1),
        "discount_pct": (1, 1),
        "line_total": (0, 1),
    }


def test_number_outside_tolerance_and_wrong_strength_are_wrong(write_source, store, read_model):
    ingest_lines(write_source, store, line("paracetamol"))
    expected = {
        "active_ingredients": [
            {"inn": "paracetamol", "strength_value": 400, "strength_unit": "mg"}
        ],
        "unit_price": 0.0094,
    }

    report = score([gold(expected)], read_model)

    assert accuracy(report.overall.fields) == {"active_ingredients": (0, 1), "unit_price": (0, 1)}


def test_scores_quotation_fields_once_per_source(write_source, store, read_model):
    ingest_lines(write_source, store, line("paracetamol"), line("ibuprofen"))
    source = gold(gold_line("paracetamol"), gold_line("ibuprofen"))
    source.quotation = {"supplier": "SYNTHETIC PHARMA LTD.", "quote_ref": "Q-2", "currency": "USD"}

    report = score([source], read_model)

    assert accuracy(report.overall.quotation_fields) == {
        "supplier": (1, 1),
        "quote_ref": (0, 1),
        "currency": (1, 1),
    }
    assert [(w.line_no, w.field, w.expected, w.actual) for w in report.wrong_fields] == [
        (None, "quote_ref", "Q-2", "Q-1")
    ]


def test_reports_the_share_of_wrong_fields_that_were_flagged(write_source, store, read_model):
    # pack_size is wrong and its evidence doesn't hold it, so it is flagged; unit_price is
    # wrong but verified against its span, so nothing warns the reviewer.
    ingest_lines(write_source, store, line("paracetamol", pack_size=30, unit_price=0.0091))
    expected = gold_line("paracetamol", uom="tablet", pack_size=20, unit_price=0.0094)

    report = score([gold(expected)], read_model)

    flagging = report.overall.flagging
    assert (flagging.wrong, flagging.flagged) == (2, 1)
    assert [(w.field, w.expected, w.actual, w.flagged) for w in report.wrong_fields] == [
        ("pack_size", 20, 30.0, True),
        ("unit_price", 0.0094, 0.0091, False),
    ]


def test_reports_review_load_including_flags_on_correct_fields(write_source, store, read_model):
    # A correct unit price whose span doesn't contain it: flagged though right.
    ingest_lines(write_source, store, line("paracetamol", spans={"unit_price": "0.0095"}))
    expected = gold_line("paracetamol", uom="tablet", pack_size=20, unit_price=0.0091)

    report = score([gold(expected)], read_model)

    load = report.overall.review_load
    assert (load.lines, load.needs_review) == (1, 1)
    # Every field verification ran on: the four line fields plus the inherited currency.
    assert (load.fields, load.flagged_fields) == (5, 1)
    assert (load.correct_fields, load.flagged_correct_fields) == (4, 1)
