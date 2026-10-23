# Supplier Document Intelligence

Turning heterogeneous supplier documents (exports, PDFs, emails, scans) into reviewable quotation records for Axmed's medicines marketplace.

## Documents

**Source**:
One file received from a supplier or customer system (a PDF, JSON export, email, image, spreadsheet). Several Sources may carry the same Quotation.
_Avoid_: File, input, attachment

**Quotation**:
A supplier's priced offer to Axmed, identified by supplier + quote reference, with terms that apply to all its lines (currency, Incoterm, validity, RFQ reference). Price lists count as Quotations.
_Avoid_: Quote, offer, proforma (as distinct concepts)

**Quotation line**:
One priced product within a Quotation.
_Avoid_: Item, record, row

**Incoterm**:
The standard trade term (EXW, FCA, FOB, CIF, CIP, ...) stating what a Quotation's prices include: who pays freight and insurance, and where the goods change hands. Prices under different Incoterms are not like-for-like.
_Avoid_: Delivery terms, shipping terms

## Products

**Product**:
A medicine identified by its Active ingredients, their Strengths, and its Dosage form, independent of supplier, brand, or pack.
_Avoid_: SKU, item

**Brand name**:
The supplier's commercial name for a Product (e.g. "Dolostop 500").
_Avoid_: Trade name, product name (when the generic is meant)

**INN**:
International Nonproprietary Name: the WHO generic name of an active substance's active moiety, without salt or ester (e.g. "amoxicillin", not "amoxicillin trihydrate").
_Avoid_: Generic name, molecule

**Active ingredient**:
One active substance in a Product, as an INN plus its Strength. Combination products have several.
_Avoid_: API, component

**Strength**:
The amount of one Active ingredient per unit or per volume (e.g. "500 mg", "250 mg / 5 mL", "100 mcg / actuation").
_Avoid_: Dose, concentration

**Dosage form**:
The physical form a Product is administered in (tablet, oral suspension, solution for injection, inhaler).
_Avoid_: Form, presentation

## Pricing

**UOM**:
Unit of measure: the smallest countable unit a price can refer to (tablet, vial, ampoule, bottle, pen, inhaler).
_Avoid_: Unit (unqualified)

**Pack**:
The saleable unit containing a fixed number of UOMs.
_Avoid_: Box, carton, presentation

**Pack size**:
The number of UOMs in one Pack.
_Avoid_: Units per pack, pack quantity

**Unit price**:
Price per UOM.
_Avoid_: Price per uom, per-unit cost

**Pack price**:
Price per Pack.
_Avoid_: Box price

**Price tier**:
A Pack price or Unit price that applies only within a stated order-volume range.
_Avoid_: Volume band, bracket

## Review

**Conflict**:
Disagreement between Sources about the same Quotation line (same supplier, quote reference, and Product). One value is wrong, so a reviewer must resolve it.
_Avoid_: Discrepancy, mismatch (when meaning across suppliers)

**Price comparison**:
Quotation lines from different Quotations for the same Product, set side by side on a normalized price. Differences are expected and not errors.
_Avoid_: Conflict

**Evidence**:
The verbatim span of a Source that a field value was read from.
_Avoid_: Citation, quote

**Second read**:
A re-extraction of a Source by a stronger model, compared field by field with the first read. It runs for image Sources and for Sources with a line whose critical checks failed.
_Avoid_: Double-check, re-run, verification pass

**Review reason**:
A plain-language statement of why a Quotation line needs a human to look at it.
_Avoid_: Warning, error
