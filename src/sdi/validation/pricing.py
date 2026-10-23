"""Calculated prices for one Quotation line (ADR-0002).

The extractor records only what the document states; everything here is derived in code
and stored next to the stated values, never in place of them.
"""

from datetime import date

from pydantic import BaseModel

from sdi.extraction.schema import PriceTier, QuotationLine
from sdi.fx import FxSource


class DerivedPrices(BaseModel):
    # The line's Unit and Pack price: as stated (on the line, else in its lowest-volume Price
    # tier), or calculated from the other and Pack size.
    headline_unit_price: float | None = None
    headline_pack_price: float | None = None
    # The headline prices after the stated discount; empty when no discount is stated.
    net_unit_price: float | None = None
    net_pack_price: float | None = None
    # The headline Unit price in USD at the issue-date rate, before discount, and the rate used.
    usd_per_uom: float | None = None
    fx_rate: float | None = None
    fx_rate_date: date | None = None
    fx_source: str | None = None


def usable_pack_size(line: QuotationLine) -> float | None:
    """The Pack size, unless the rules reject it (not a positive whole number): a rejected
    value calculates and cross-checks nothing."""
    size = line.pack_size
    return size if size is not None and size > 0 and size.is_integer() else None


def usable_discount(line: QuotationLine) -> float | None:
    """The discount percentage, unless the rules reject it (outside 0-100%)."""
    discount = line.discount_pct
    return discount if discount is not None and 0 <= discount <= 100 else None


def headline_tier(line: QuotationLine) -> PriceTier | None:
    """The lowest-volume priced tier, when the line states no price of its own."""
    if line.unit_price is not None or line.pack_price is not None:
        return None
    priced = [t for t in line.price_tiers if t.unit_price is not None or t.pack_price is not None]
    return min(priced, key=lambda t: t.min_qty or 0, default=None)


def derive_prices(line: QuotationLine) -> DerivedPrices:
    stated = headline_tier(line) or line
    unit, pack = stated.unit_price, stated.pack_price
    pack_size = usable_pack_size(line)
    if pack_size is not None:
        if unit is None and pack is not None:
            unit = pack / pack_size
        elif pack is None and unit is not None:
            pack = unit * pack_size
    prices = DerivedPrices(headline_unit_price=unit, headline_pack_price=pack)
    if (discount := usable_discount(line)) is not None:
        keep = 1 - discount / 100
        prices.net_unit_price = None if unit is None else unit * keep
        prices.net_pack_price = None if pack is None else pack * keep
    return prices


def convert_to_usd(
    prices: DerivedPrices, currency: str, issue_date: date | None, fx: FxSource
) -> str | None:
    """Fill in USD per UOM at the issue-date rate. Returns why no rate is available, or None.
    A missing rate leaves the USD value empty: it is never estimated."""
    unit = prices.headline_unit_price
    if unit is None:
        return None
    if currency == "USD":  # no conversion, so no rate date or source
        prices.usd_per_uom, prices.fx_rate = unit, 1.0
        return None
    if issue_date is None:
        return f"No issue date to take a {currency}→USD rate on"
    rate = fx(currency, issue_date)
    if rate is None:
        return f"No {currency}→USD rate available for {issue_date}"
    prices.usd_per_uom = unit * rate.rate
    prices.fx_rate, prices.fx_rate_date, prices.fx_source = rate.rate, rate.rate_date, rate.source
    return None
