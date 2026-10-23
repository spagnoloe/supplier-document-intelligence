"""Calculated prices for one Quotation line (ADR-0002).

The extractor records only what the document states; everything here is derived in code
and stored next to the stated values, never in place of them.
"""

from pydantic import BaseModel

from sdi.extraction.schema import QuotationLine


class DerivedPrices(BaseModel):
    # The line's Unit and Pack price: as stated, or calculated from the other and Pack size.
    headline_unit_price: float | None = None
    headline_pack_price: float | None = None


def derive_prices(line: QuotationLine) -> DerivedPrices:
    unit, pack = line.unit_price, line.pack_price
    # A Pack size the rules reject (not a positive whole number) calculates nothing.
    pack_size = line.pack_size
    if pack_size is not None and (pack_size <= 0 or not pack_size.is_integer()):
        pack_size = None
    if pack_size is not None:
        if unit is None and pack is not None:
            unit = pack / pack_size
        elif pack is None and unit is not None:
            pack = unit * pack_size
    return DerivedPrices(headline_unit_price=unit, headline_pack_price=pack)
