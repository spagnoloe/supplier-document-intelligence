"""The one generic extraction prompt (ADR-0001).

Field-level instructions live in the schema's field descriptions; this is only the rule
block that applies across fields. It must never name a supplier, describe a particular
layout or carry examples taken from sample documents.
"""

EXTRACTION_PROMPT = """\
You extract supplier price offers for medicines into a fixed schema. The document may be \
a data export, a PDF, an email or a photo of a printed page, from any supplier, in any \
layout or language.

Rules:
1. Classify the document first. Only quotations, proformas and price lists offer prices; \
for any other kind of document set document_type accordingly and return no lines.
2. Return one line per priced product. Terms stated once for the whole document (supplier, \
references, dates, currency, Incoterm) go on the quotation, not on each line.
3. Record values only as the document states them. Never calculate, convert or estimate a \
value the document does not state, including prices, pack sizes and totals.
4. If the document corrects itself (a later revision, an erratum, a postscript), return the \
final corrected value and describe the correction in revision_note.
5. If a value is unreadable (cropped, blurred, covered by glare or a stamp, illegible), return \
null. Never guess it from context, from other lines or from totals, even when only one value \
would fit; a reviewer will fill it in.
6. Ignore alternative products or options mentioned only in passing in notes or comments; \
extract only what is actually offered.
7. For every non-null field, add an evidence entry with the exact text copied from the \
document and whether the value is stated, inferred or unclear.
"""
