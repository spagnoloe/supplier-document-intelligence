"""Eval: score the Quotation lines in a database against a hand-labelled gold set.

The gold set says, per Source, what a careful human reads from it. A field the gold set
leaves out is not scored; a field it sets to null is expected to come back empty (e.g. a
price hidden by glare). Scores are reported per Source type and overall.
"""

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from sdi.read_model import LineView, ReadModel

# Numbers within 0.5% of the gold value count as correct: documents round, readers don't.
NUMBER_TOLERANCE = 0.005

# Terms of a Quotation, scored from its first extracted line (every line carries them).
QUOTATION_FIELDS = ("supplier", "quote_ref", "currency", "incoterm", "incoterm_place")

_TIER_FIELDS = ("min_qty", "max_qty", "unit_price", "pack_price")


class GoldSource(BaseModel):
    source: str  # file name of the Source
    source_type: str  # json, pdf, email, image, ...: the breakdown the report uses
    quotation: dict[str, object]
    lines: list[dict[str, object]]
    notes: str | None = None


class LineMatching(BaseModel):
    expected: int = 0
    found: int = 0
    missed: int = 0
    extra: int = 0


class FieldAccuracy(BaseModel):
    correct: int = 0
    scored: int = 0


class Flagging(BaseModel):
    """Wrong line fields on matched lines, and how many of them were flagged: failed by
    verification or named by a Review reason. An unflagged wrong field fails silently."""

    wrong: int = 0
    flagged: int = 0


class ReviewLoad(BaseModel):
    lines: int = 0  # every extracted line, matched or not
    needs_review: int = 0
    fields: int = 0  # every field verification ran on, across those lines
    flagged_fields: int = 0
    # Scored fields with a correct, non-empty value; and those flagged anyway (false alarms).
    # A correctly empty field isn't counted: flagging a hidden price is the point.
    correct_fields: int = 0
    flagged_correct_fields: int = 0


class Scores(BaseModel):
    lines: LineMatching = LineMatching()
    # Line fields on matched lines, and Quotation terms once per Source.
    fields: dict[str, FieldAccuracy] = {}
    quotation_fields: dict[str, FieldAccuracy] = {}
    flagging: Flagging = Flagging()
    review_load: ReviewLoad = ReviewLoad()


class WrongField(BaseModel):
    source: str
    line_no: int
    field: str
    expected: object
    actual: object
    flagged: bool


class EvalReport(BaseModel):
    by_type: dict[str, Scores]
    overall: Scores
    wrong_fields: list[WrongField]


def load_gold(folder: Path) -> list[GoldSource]:
    return [
        GoldSource.model_validate(json.loads(path.read_text()))
        for path in sorted(folder.glob("*.json"))
    ]


def score(gold: list[GoldSource], read_model: ReadModel) -> EvalReport:
    extracted: dict[str, list[LineView]] = defaultdict(list)
    for line in read_model.quotation_lines(corrected=False):
        extracted[Path(line.source).name].append(line)

    by_type: dict[str, Scores] = defaultdict(Scores)
    overall = Scores()
    wrong_fields = []
    for source in gold:
        lines = extracted[source.source]
        pairs = match_lines(source.lines, lines)
        checked = {line.id: _checked_fields(read_model, line) for line in lines}
        flags = {line_id: flagged for line_id, (_, flagged) in checked.items()}
        outcomes = []  # (field, correct, has a value, flagged) per scored line field
        for gold_line, line in pairs:
            for field, expected in gold_line.items():
                actual = getattr(line, field)
                correct = matches(field, expected, actual)
                flagged = field in flags[line.id]
                outcomes.append((field, correct, not _empty(actual), flagged))
                if not correct:
                    wrong_fields.append(
                        WrongField(
                            source=source.source,
                            line_no=line.line_no,
                            field=field,
                            expected=expected,
                            actual=actual,
                            flagged=flagged,
                        )
                    )
        for scores in (by_type[source.source_type], overall):
            scores.lines.expected += len(source.lines)
            scores.lines.found += len(pairs)
            scores.lines.missed += len(source.lines) - len(pairs)
            scores.lines.extra += len(lines) - len(pairs)
            for field, expected in source.quotation.items():
                actual = getattr(lines[0], field) if lines else None
                _count(scores.quotation_fields, field, matches(field, expected, actual))
            for field, correct, has_value, flagged in outcomes:
                _count(scores.fields, field, correct)
                if not correct:
                    scores.flagging.wrong += 1
                    scores.flagging.flagged += flagged
                elif has_value:
                    scores.review_load.correct_fields += 1
                    scores.review_load.flagged_correct_fields += flagged
            load = scores.review_load
            load.lines += len(lines)
            load.needs_review += sum(line.review_status == "needs_review" for line in lines)
            load.fields += sum(len(fields) for fields, _ in checked.values())
            load.flagged_fields += sum(len(flagged) for _, flagged in checked.values())
    return EvalReport(by_type=dict(by_type), overall=overall, wrong_fields=wrong_fields)


def _checked_fields(read_model: ReadModel, line: LineView) -> tuple[set[str], set[str]]:
    """The line's fields verification ran on or a Review reason names, and of those the
    flagged ones: failed by verification, or named by a Review reason (even one that doesn't
    force review, such as an inferred value: the reviewer still sees it)."""
    results = {f.field: f.result for f in read_model.line_detail(line.id).fields}
    named = {r.field for r in line.review_reasons if r.field is not None}
    flagged = {field for field, result in results.items() if result == "failed"} | named
    return set(results) | named, flagged


def matches(field: str, expected: Any, actual: Any) -> bool:
    """Whether an extracted value agrees with the gold value. A gold value of
    {"any_of": [...]} accepts any of the listed values."""
    if isinstance(expected, dict) and "any_of" in expected:
        return any(matches(field, option, actual) for option in expected["any_of"])
    if expected is None or actual is None:
        return expected is None and _empty(actual)
    if field == "active_ingredients":
        return _ingredient_set(expected) == _ingredient_set(actual)
    if field == "price_tiers":
        return len(expected) == len(actual) and all(
            all(matches(k, tier.get(k), got.get(k)) for k in _TIER_FIELDS)
            for tier, got in zip(expected, actual, strict=True)
        )
    if isinstance(expected, int | float) and isinstance(actual, int | float):
        return _close(expected, actual)
    return _norm(expected) == _norm(actual)


def _empty(value: object) -> bool:
    return value is None or value == []


def _close(expected: float, actual: float) -> bool:
    return abs(actual - expected) <= NUMBER_TOLERANCE * abs(expected)


def _ingredient_set(ingredients: list[dict]) -> list[tuple[str, float | None, str]]:
    """Sorted (INN, strength value, strength unit). Strengths are exact; unit spacing and
    case don't count ("mg/5 mL" = "MG / 5ml")."""
    return sorted(
        (
            _norm(i.get("inn")),
            None if i.get("strength_value") is None else round(float(i["strength_value"]), 6),
            _norm(i.get("strength_unit")).replace(" ", ""),
        )
        for i in ingredients
    )


def _count(fields: dict[str, FieldAccuracy], field: str, correct: bool) -> None:
    accuracy = fields.setdefault(field, FieldAccuracy())
    accuracy.scored += 1
    accuracy.correct += correct


def match_lines(
    expected: list[dict[str, object]], extracted: list[LineView]
) -> list[tuple[dict[str, object], LineView]]:
    """Pair each expected line with at most one extracted line of the same Product: the most
    INNs in common, then the same Brand name, then the nearest position. Salt words are
    ignored here (the INN itself is scored separately), so a line is still matched when its
    INN carries a salt."""
    candidates = []
    for i, gold_line in enumerate(expected):
        gold_inns = {_first_word(a["inn"]) for a in _ingredients(gold_line)}
        for j, line in enumerate(extracted):
            shared = len(gold_inns & {_first_word(inn) for inn in line.inns})
            same_brand = _norm(gold_line.get("brand_name")) == _norm(line.brand_name) != ""
            if shared or same_brand:
                candidates.append(((-shared, not same_brand, abs(i - j)), i, j))
    pairs, used_gold, used_extracted = [], set(), set()
    for _, i, j in sorted(candidates):
        if i not in used_gold and j not in used_extracted:
            used_gold.add(i)
            used_extracted.add(j)
            pairs.append((expected[i], extracted[j]))
    return pairs


def _ingredients(gold_line: dict[str, object]) -> list[dict]:
    ingredients = gold_line.get("active_ingredients") or []
    assert isinstance(ingredients, list)
    return ingredients


def _first_word(inn: str | None) -> str:
    words = _norm(inn).split()
    return words[0] if words else ""


def _norm(value: object) -> str:
    """Lower case, punctuation and runs of spaces collapsed to one space."""
    if value is None:
        return ""
    return " ".join("".join(c if c.isalnum() else " " for c in str(value).lower()).split())
