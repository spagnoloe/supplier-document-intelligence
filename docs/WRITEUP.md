# Write-up: supplier document intelligence

**Architecture.** One LangGraph graph per Source: **load** (by file type) → **extract** (Gemini structured output into one Pydantic schema) → **validate** (deterministic checks) → **second read** (conditional) → **store** (SQLite). After the batch, **reconcile** records Conflicts and Price comparisons. A Streamlit UI and `sdi export` sit on a read model. The [ADRs](adr/):
- no per-supplier logic (0001);
- the model extracts only stated values, and code derives the rest (0002);
- a *Conflict* (copies of one Quotation disagree) is not a *Price comparison* (one Product across Quotations) (0003);
- review comes from per-field verification, not the model's self-confidence (0004).

**Model:** Gemini on the free tier (0005). The take-home had a zero budget, and a Claude subscription can't be used by a custom pipeline. Flash-lite extracts; the stronger flash model does the second read.

**Extensibility.**
- *New supplier, layout or customer-system export:* no code change. Loaders handle format; one generic prompt (no supplier names, layouts or examples) handles meaning.
- *New file type:* one loader module, plus one registry import in `src/sdi/loaders/__init__.py` and its dependency. Excel proves it: [commit `51f20e9`](https://github.com/spagnoloe/supplier-document-intelligence/commit/51f20e9) changed no other module, and its lines matched the PDF they were copied from. An unknown type fails loudly, for that Source only.
- *Same product from two systems:* `src/sdi/reconcile/` links Sources by normalized supplier and quote reference, and matches lines by Product key (INNs, strengths, dosage form). Disagreements become side-by-side Conflicts sent to review; nothing is auto-merged.

**Framework.** *Bought:* a conditional edge for the second read; a `RetryPolicy` limited to structured-output errors; `graph.batch` with a concurrency cap and per-Source failure isolation; Pydantic structured output; and an SQLite LLM cache that makes the eval free and reproducible. *Cost:* state boilerplate for a mostly linear flow, and leaks. The cache stores responses before parsing, so retries replayed the malformed answer until each got its own seed; a same-model second read hit the first read's cache entry. Gemini can't express open-ended dict keys, so evidence became a list. LangSmith tracing is available through env vars but unused.

**Email and images vs structured Sources.** One prompt for all; verification differs.
- *Text* (JSON, PDF, email, Excel): each value has a verbatim evidence span. Code finds the span in the text and checks the value against it.
- *Email:* headers plus the plain-text body. Final values are taken after corrections, and a `revision_note` forces review; this catches the P.S. price change.
- *Images:* direct vision with no OCR, and `null` rather than guesses for unreadable values. Values are checked by arithmetic (unit × pack; qty × price × (1 − discount) = total), against their own span, and by a second read. An unverified critical field keeps the line in review.

**Uncertainty.** Each field is `verified`, `unverified` or `failed`, with stable reason codes. A line is `needs_review` when a critical field fails (or is unverified on an image), or when it is in a Conflict or was revised. Missing FX rates leave the USD price empty; they are never estimated.

**Eval** ([results](../eval/results.md), 9 hand-labelled Sources): 43/43 lines found. Field accuracy is 97% (JSON, email and Excel 100%, PDF 98%, images 89%). 10 of 17 wrong fields were flagged, and 47% of lines need review. The 7 unflagged errors are non-critical fields on the glare scan plus one dosage-form wording.

**Production readiness.**
- *Cost:* second reads only where needed. For large sheets, use a column mapping cached per structure fingerprint instead of whole-table prompts.
- *Latency:* queue-fed workers, rate-limited per provider.
- *Monitoring:* tracing, plus drift alarms on reason-code rates, review load and correction rate per supplier. Corrections feed the gold set.
- *PII:* the free tier may train on inputs. Use a no-training paid tier with data residency and a DPA, redact personal data from emails, and encrypt Sources at rest.
- *Review UX:* FastAPI and a real frontend with authentication, the document shown beside the line with spans highlighted, and earlier decisions suggested when a Source is re-read.
- *Models:* provider-agnostic, with fallbacks and a second read from another model family.

**CI/CD and infrastructure.** CI runs ruff, mypy, pytest (no model calls) and `sdi eval --cached-only` against a score threshold. A live eval runs on any prompt, schema or model change, which the extraction fingerprint already detects. Deploy as containers on Postgres, with Sources in object storage, scaled horizontally behind a queue; fingerprints keep re-deliveries idempotent.

**Limitations.** Alternative offers in notes aren't extracted. Tables are serialized whole into the prompt. There's no OCR confidence. The second read uses a single provider, so its errors are correlated (the committed eval used one model for both reads, because of quota). Price comparison needs an exact dosage-form match. A re-read restarts review. A PDF mixing text and scanned pages is read as text. Salt words come from a fixed list. Non-ECB currencies get no USD price.

**The suggested data model.** A flat record per product can't hold a quotation. Without a supplier, reference or date, copies can't be linked; without Incoterm or validity, prices aren't comparable. It has no pack size, UOM, tiers or discounts. One `inn` string can't hold combinations or separate salt from moiety, and without strength and dosage form an inhaler and a syrup look alike. `price_per_uom` forces arithmetic on per-box prices, and nothing records certainty or provenance. *Improved:* a Quotation header with terms and a list of lines; `active_ingredients[]` (INN plus strength); dosage form; the stated Unit and Pack prices, with code-derived values and FX provenance; and per-field evidence, verification and Review reasons, each pointing to a Source and line. *Still missing:* manufacturer vs supplier, WHO-PQ/SRA status, shelf life, lead time, MOQ, and product IDs (GTIN, ATC).

*Assumptions:* `price_per_uom` is the stated Unit price before discount, at the lowest-volume tier, in USD at the issue-date ECB rate. INN is the WHO INN of the active moiety. A line quantity may count UOMs or Packs.
