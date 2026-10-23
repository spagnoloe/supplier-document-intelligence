# Conflict and Price comparison are separate concepts

Reconciliation produces two things. A **Conflict** is a disagreement between Sources about the same Quotation line (same supplier, quote reference and Product); one value is wrong, so the line goes to review. A **Price comparison** sets lines for the same Product from different Quotations side by side, normalized to USD per UOM at the quotation's issue-date FX rate, with Incoterms shown and a warning when they differ. Different suppliers quoting different prices is a market, not an error, so treating it as a Conflict would flood review.

## Consequences

- Sources are linked only by supplier + quote reference. A Source with no readable reference stays its own Quotation, with review reason `NO_QUOTE_REFERENCE`, rather than being fuzzily merged. Both halves of the key are required: a Source with no supplier stays unlinked with `NO_SUPPLIER`. The reason is a note, not a review trigger: the Source is still verified on its own, and ADR-0004's review causes don't include it.
- A value one Source leaves null isn't a disagreement, so an unreadable copy can't push its clean sibling into review.
- Quotation terms (Incoterm, place, issue date, validity, RFQ reference) are compared like line fields. A term that differs puts every line of each Source stating it in review, since it changes what all of that Source's prices mean.
- Price comparison requires an exact match on INN set, strengths and standardized dosage form. Some genuinely comparable pairs (tablet vs film-coated tablet) are missed; comparing non-equivalent products would be worse.
- The Product key ignores trailing salt and hydrate words in an INN, since the model doesn't always strip them. The list is pharmaceutical vocabulary, not drawn from the pack. A name made only of salt words ("magnesium sulfate") stays whole, since there the salt is the product. Two genuinely different salts of one moiety at the same stated strength would be grouped, or matched as one line of a Quotation. A false Conflict goes to review, where it is cheap to dismiss; a missed match hides one.
- A missing Incoterm counts as differing from any stated one. What its price includes is unknown, so the group can't be called like-for-like.
- Reconciliation never merges or picks a winner.
