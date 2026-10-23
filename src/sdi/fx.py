"""FX rate source: (currency, date) → rate to USD, or None when no rate is available."""

import json
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import date

from pydantic import BaseModel


class FxRate(BaseModel):
    rate: float  # USD per one unit of the currency
    rate_date: date  # the date the rate was published for; may precede the date asked
    source: str


FxSource = Callable[[str, date], FxRate | None]


FRANKFURTER_URL = "https://api.frankfurter.dev/v1"


def frankfurter(currency: str, on: date) -> FxRate | None:
    """ECB reference rates from Frankfurter. Thin adapter, not unit tested.

    For a weekend or holiday Frankfurter answers with the last published rate, and says which
    date that was. An unknown currency or an unreachable service is no rate, not an error.
    """
    query = urllib.parse.urlencode({"base": currency, "symbols": "USD"})
    # The service rejects urllib's default User-Agent with 403 Forbidden.
    request = urllib.request.Request(
        f"{FRANKFURTER_URL}/{on.isoformat()}?{query}", headers={"User-Agent": "sdi"}
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as r:
            body = json.load(r)
        return FxRate(
            rate=body["rates"]["USD"],
            rate_date=date.fromisoformat(body["date"]),
            source="Frankfurter (ECB reference rates)",
        )
    except (OSError, ValueError, KeyError):
        return None
