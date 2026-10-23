# Review status comes from field verification, not model-reported confidence

Each extracted field is marked verified, unverified or failed by independent checks: the evidence span found verbatim in source text, arithmetic cross-checks, rule checks, and, for images, agreement between two independent reads. A line is `needs_review` when a critical field (price, currency, pack size, UOM, ingredients/strength) fails, or is unverified on an image Source, or when the line is in a Conflict or carries a revision. Self-reported float confidence was dropped because verbalized LLM confidence is poorly calibrated. A categorical `stated / inferred / unclear` label is kept as an input to review reasons. We rejected "all image lines need review" because it makes the image path pointless; verified image lines can be auto-accepted.

## Consequences

- The eval measures flagging quality (share of wrong fields that were flagged) alongside review load (% lines `needs_review`, % fields flagged, flags on correct fields), so the policy can't win by flagging everything.
- Both image reads come from the same provider (see ADR-0005), so their errors are less independent than cross-family reads would be.
