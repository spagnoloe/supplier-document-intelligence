# Conflict and Price comparison are separate concepts

Reconciliation produces two things. A **Conflict** is a disagreement between Sources about the same Quotation line (same supplier, quote reference and Product); one value is wrong, so the line goes to review. A **Price comparison** sets lines for the same Product from different Quotations side by side, normalized to USD per UOM at the quotation's issue-date FX rate, with Incoterms shown and a warning when they differ. Different suppliers quoting different prices is a market, not an error, so treating it as a Conflict would flood review.

## Consequences

- Sources are linked only by supplier + quote reference. A Source with no readable reference stays its own Quotation, with review reason `NO_QUOTE_REFERENCE`, rather than being fuzzily merged.
- Price comparison requires an exact match on INN set, strengths and standardized dosage form. Some genuinely comparable pairs (tablet vs film-coated tablet) are missed; comparing non-equivalent products would be worse.
- Reconciliation never merges or picks a winner.
