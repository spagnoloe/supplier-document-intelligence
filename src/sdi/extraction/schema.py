"""Extraction schema: what the LLM fills in for one Source (ADR-0002).

Field descriptions are the field-level instructions sent to the model, so they must stay
generic: no supplier names, layouts or examples taken from any sample document.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

Certainty = Literal["stated", "inferred", "unclear"]

DocumentType = Literal[
    "quotation", "price_list", "order_confirmation", "invoice", "product_specification", "other"
]
SUPPORTED_DOCUMENT_TYPES: frozenset[str] = frozenset({"quotation", "price_list"})

QuotationField = Literal[
    "supplier",
    "quote_ref",
    "rfq_ref",
    "issue_date",
    "valid_until",
    "currency",
    "incoterm",
    "incoterm_place",
]

LineField = Literal[
    "brand_name",
    "active_ingredients",
    "dosage_form",
    "uom",
    "pack_size",
    "unit_price",
    "pack_price",
    "currency",
    "discount_pct",
    "quantity",
    "line_total",
    "price_tiers",
]


class FieldEvidence(BaseModel):
    """Where one field's value came from, and how directly the document states it."""

    field: str = Field(description="Name of the field this evidence supports.")
    span: str | None = Field(
        description="The exact text copied verbatim from the document that the value was "
        "read from, without rewording or reformatting numbers. Null if there is no such text."
    )
    certainty: Certainty = Field(
        description="'stated' if the document gives this value directly for this field; "
        "'inferred' if it was taken from context (e.g. a currency from the document header "
        "applied to a line); 'unclear' if the text is partly unreadable or ambiguous."
    )


class QuotationFieldEvidence(FieldEvidence):
    field: QuotationField


class LineFieldEvidence(FieldEvidence):
    field: LineField


class ActiveIngredient(BaseModel):
    name_as_written: str = Field(
        description="The ingredient name exactly as the document writes it."
    )
    inn: str = Field(
        description="WHO International Nonproprietary Name of the active moiety, lower case, "
        "with salts, esters and hydrates removed (e.g. 'metformin', not 'metformin "
        "hydrochloride')."
    )
    strength_value: float | None = Field(
        default=None, description="Numeric amount of this ingredient per unit or per volume."
    )
    strength_unit: str | None = Field(
        default=None,
        description="Unit of the strength, e.g. 'mg', 'g', 'mcg', 'IU', 'mg/5 mL', 'mg/mL', "
        "'mcg/actuation'.",
    )


class PriceTier(BaseModel):
    min_qty: float | None = Field(default=None, description="Lowest order quantity of the tier.")
    max_qty: float | None = Field(
        default=None, description="Highest order quantity of the tier; null if open-ended."
    )
    unit_price: float | None = Field(
        default=None, description="Price per UOM in this tier, only if the document states it."
    )
    pack_price: float | None = Field(
        default=None, description="Price per Pack in this tier, only if the document states it."
    )


class QuotationLine(BaseModel):
    """One priced product."""

    brand_name: str | None = Field(
        default=None, description="Supplier's commercial product name, if any. Not the INN."
    )
    active_ingredients: list[ActiveIngredient] = Field(
        default_factory=list,
        description="One entry per active substance; combination products have several.",
    )
    dosage_form: str | None = Field(
        default=None,
        description="Standardized dosage form in plain English, sentence case, e.g. 'Tablet', "
        "'Film-coated tablet', 'Capsule', 'Oral suspension', 'Solution for injection', "
        "'Inhalation aerosol'. Translate if the document is in another language.",
    )
    uom: str | None = Field(
        default=None,
        description="Unit of measure: the smallest countable unit a price can refer to, "
        "singular, lower case, e.g. 'tablet', 'capsule', 'vial', 'ampoule', 'bottle'.",
    )
    pack_size: float | None = Field(
        default=None, description="Number of UOMs in one Pack (the saleable unit)."
    )
    unit_price: float | None = Field(
        default=None,
        description="Price per UOM, only if the document states it. Never compute it.",
    )
    pack_price: float | None = Field(
        default=None,
        description="Price per Pack, only if the document states it. Never compute it.",
    )
    currency: str | None = Field(
        default=None,
        description="ISO 4217 code, only if this line states its own currency. Leave null "
        "when the line uses the quotation currency.",
    )
    discount_pct: float | None = Field(
        default=None, description="Discount stated for this line, as a percentage (10 = 10%)."
    )
    quantity: float | None = Field(
        default=None, description="Quantity ordered or quoted for this line, if stated."
    )
    line_total: float | None = Field(
        default=None, description="Total amount stated for this line, if any."
    )
    price_tiers: list[PriceTier] = Field(
        default_factory=list,
        description="Volume price tiers, if prices depend on order quantity. Empty otherwise.",
    )
    revision_note: str | None = Field(
        default=None,
        description="If the document corrects or revises a value for this line, describe "
        "the change (old value, new value, where it was stated). Null otherwise.",
    )
    evidence: list[LineFieldEvidence] = Field(
        default_factory=list,
        description="One entry per non-null field above, with its verbatim source text.",
    )


class Quotation(BaseModel):
    """A supplier's priced offer: terms that apply to every line, plus its lines."""

    document_type: DocumentType = Field(
        description="What kind of document this is. Price lists and quotations / proformas "
        "offering prices are 'quotation' or 'price_list'."
    )
    supplier: str | None = Field(default=None, description="Legal or trading name of the supplier.")
    quote_ref: str | None = Field(
        default=None, description="The supplier's own reference number for this offer."
    )
    rfq_ref: str | None = Field(
        default=None, description="The buyer's request-for-quotation reference, if quoted."
    )
    issue_date: date | None = Field(default=None, description="Date the offer was issued.")
    valid_until: date | None = Field(
        default=None, description="Last date the offer is valid, if stated or given as a period."
    )
    currency: str | None = Field(
        default=None, description="ISO 4217 code of the currency the prices are in."
    )
    incoterm: str | None = Field(
        default=None, description="Incoterm code, upper case, e.g. 'EXW', 'FCA', 'FOB', 'CIF'."
    )
    incoterm_place: str | None = Field(
        default=None, description="Named place that goes with the Incoterm."
    )
    lines: list[QuotationLine] = Field(
        default_factory=list, description="Every priced product, one entry per product."
    )
    evidence: list[QuotationFieldEvidence] = Field(
        default_factory=list,
        description="One entry per non-null quotation-level field, with its verbatim source text.",
    )
