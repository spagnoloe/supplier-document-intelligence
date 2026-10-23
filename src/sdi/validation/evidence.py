"""Evidence check: is a verbatim span really in the source text?

Spans and text are compared after normalization, so a span still matches when the model
changed only whitespace, quoting, letter case or how a number is written
("1,250.00" = "1250", `"min_qty": 10` = `min_qty: 10`).
"""

import re
import unicodedata
from decimal import Decimal

_NUMBER = re.compile(r"\d+(?:[.,']\d+)*")
_IGNORED = re.compile(r"[\s\"'`\u2018\u2019\u201c\u201d]+")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()
    # Numbers are fenced so a span "25" can't match inside "1250".
    text = _NUMBER.sub(lambda m: f"#{_canonical(m.group())}#", text)
    return _IGNORED.sub("", text)


class SourceText:
    """A Source's text, normalized once for every span checked against it."""

    def __init__(self, text: str):
        self._normalized = normalize(text)
        self.numbers = numbers_in(text)

    def contains(self, span: str) -> bool:
        needle = normalize(span)
        return bool(needle) and needle in self._normalized


def numbers_in(span: str) -> set[Decimal]:
    """Every value the numbers in a span can be read as, separator conventions included."""
    return {value for token in _NUMBER.findall(span) for value in _readings(token)}


def _canonical(token: str) -> str:
    return format(_readings(token)[0].normalize(), "f")


def _readings(token: str) -> list[Decimal]:
    """Possible values of a number token, most likely first.

    "1,250.00" and "1.250,00" are unambiguous: the last separator is the decimal point. A
    single separator is ambiguous ("1,250" is 1250 or 1.25), so both readings are kept.
    """
    token = token.replace("'", "")
    separators = [c for c in token if c in ".,"]
    if not separators:
        return [Decimal(token)]
    decimal_sep = separators[-1]
    if len(separators) > 1 and len(set(separators)) == 1:
        return [Decimal(token.replace(decimal_sep, ""))]  # "1,000,000": grouping only
    whole, _, fraction = token.rpartition(decimal_sep)
    as_decimal = Decimal(whole.replace("." if decimal_sep == "," else ",", "") + "." + fraction)
    if len(separators) > 1:
        return [as_decimal]
    as_grouping = Decimal(whole + fraction)
    if len(fraction) == 3 and whole.strip("0") and decimal_sep == ",":
        return [as_grouping, as_decimal]  # "1,250" is more often 1250 than 1.25
    return [as_decimal, as_grouping]
