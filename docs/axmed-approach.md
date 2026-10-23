# Axmed Take-Home: Approach

Document Intelligence Agent: ingest heterogeneous supplier documents, extract Quotation lines into a common schema, flag uncertainty, and hand results to a human reviewer.

**Time-box:** 4–6 hours (target ~5.5h including deliverables).
**Constraints:** Python, LangGraph, Gemini free tier (ADR-0005).
**Most heavily weighted criterion:** generalisation to new formats, suppliers and source systems.

Terms follow `GLOSSARY.md`. Decisions with lasting consequences are in `docs/adr/`.

---

## 1. Core principle: no per-supplier logic anywhere (ADR-0001)

| What arrives | Where it's handled | Code change |
|---|---|---|
| **New file type** (e.g. the first Excel file) | Loader layer | One new loader + one registry line |
| **New layout of a known type** (unseen JSON field names, new PDF layout) | Generic LLM extractor | None |
| **Same product from several Sources** | Reconciliation | None. Conflicts are flagged, never auto-merged |

- **Loaders handle format.** One per file type.
- **The extractor handles layout and meaning.** One generic prompt: no supplier names, no layout hints, no examples taken from the pack.
- **Deterministic code handles arithmetic and rules.** Derived prices, FX, cross-checks, review rules.

An unknown file extension fails loudly ("no loader for .xyz").

---

## 2. Pipeline overview

```
 one Source (PDF / JSON / email / image / xlsx)
        │
   1. LOAD        loader by file type → DocumentContent (text and/or images)
        │
   2. EXTRACT     LLM fills the schema → Quotation with QuotationLines
        │
   3. VALIDATE    evidence, arithmetic and rule checks; derived prices; FX;
        │         per-field verification → review_status + review_reasons
        │
   3b. SECOND READ  (conditional edge) for images, or when critical checks fail:
        │         a stronger model re-extracts; disagreeing fields fail
        │
   4. STORE       SQLite
        │
 ── after all Sources ──
        │
   5. RECONCILE   Conflicts (same Quotation line, Sources disagree)
        │         + Price comparison (same Product, different Quotations)
        │
   6. REVIEW      Streamlit UI over SQLite
```

Steps 1–4 are a LangGraph graph per Source, run with `graph.batch()` and a concurrency limit. Step 5 is a plain function, since it isn't a per-document flow.

**Re-runs skip unchanged Sources.** Each stored Source keeps a fingerprint of its content and of the extraction setup it was read with: the models, the prompt and the schema. A re-run reads only Sources whose fingerprint changed, plus those that failed; `--force` reads every Source again, e.g. after a change to validation. Reconciliation still runs over every stored Source. A supplier document doesn't change once received (a revised quote is a new file), so outside development a Source is read again only when the pipeline changes.

---

## 3. DocumentContent: the loader output

```python
class DocumentContent(BaseModel):
    source_path: str
    text: str | None  # anything readable as text
    images: list[bytes]  # anything that must be seen
    metadata: dict  # file type, page count, ...
```

| Source | `text` | `images` |
|---|---|---|
| Text PDF | extracted text (pdfplumber) | `[]` |
| Scanned PDF (near-zero text yield) | `None` | each page rendered to PNG |
| Photo / scan | `None` | `[the image]` |
| JSON / Excel | serialized content | `[]` |
| Email (.eml) | From/Date/Subject headers + text/plain body (HTML→text fallback) | `[]` |

The scanned-PDF fallback is generic: below 25 non-blank characters per page on average, the PDF counts as a scan and its pages are rendered at 200 dpi. A page number or scanner stamp doesn't count as a text layer, since sending it as text would fail every evidence check. The pack has no scanned PDFs, so the fallback is covered by a synthetic test, not by the sample.

**Known limitation:** the text yield is averaged over the whole PDF, so one with a text page and a scanned page counts as text and the scanned page is never seen. Handling it per page would need one Source to carry both text and images, and image-ness to be decided per page rather than per Source, which reaches into the review rules. Nothing in the pack exercises it, so it's recorded, not built.

---

## 4. Extensibility proof: the Excel loader

- Built **after** the pipeline works, in **one isolated commit** (ideally its own PR).
- That diff touches only `loaders/excel_loader.py` and a synthetic `samples/price_list.xlsx`, with rows copied from a PDF quote so the extracted values cross-check.
- If it forces changes elsewhere, that's a design leak to fix before submission.

**Result: commit `51f20e9`.** Besides the loader and the sample, the diff holds the registry import line, the `openpyxl` dependency (plus its type stubs) and the ingest-seam tests. No other module changed. The sample copies the six lines of a PDF proforma into a two-sheet price-list layout. Ingested next to that PDF, all six lines matched on every line field and on the header terms.

**Known limitation:** tables are serialized whole into the prompt. That's fine for small files. Large sheets cost tokens and risk dropped rows. The production path is an LLM-proposed column mapping cached per structure fingerprint. It's described, not built.

**Known limitation:** cells are read as their stored values. A formula cell with no saved result (files written by scripts rather than Excel) loads as empty, and number formats are lost (a `3%` cell reads as `0.03`). Production would flag uncomputed formulas and render cells with their display format.

---

## 5. Extraction schema

Quotation-level terms are separate from lines, so the LLM can't contradict itself across lines.

```python
class ActiveIngredient(BaseModel):
    name_as_written: str  # evidence-checked
    inn: str  # WHO INN of the active moiety, salt stripped
    strength_value: float | None
    strength_unit: str | None  # "mg", "mg/5 mL", "IU/mL", "mcg/actuation"


class PriceTier(BaseModel):
    min_qty: float | None
    max_qty: float | None
    unit_price: float | None
    pack_price: float | None


class FieldEvidence(BaseModel):
    field: Literal[...]  # an enum of the model's own field names
    span: str | None  # verbatim source span
    certainty: Literal["stated", "inferred", "unclear"]


class QuotationLine(BaseModel):
    brand_name: str | None
    active_ingredients: list[ActiveIngredient]
    dosage_form: str | None  # standardized term, e.g. "Film-coated tablet"
    uom: str | None  # tablet, vial, ampoule, bottle, pen ...
    pack_size: float | None  # UOMs per Pack
    unit_price: float | None  # only if stated
    pack_price: float | None  # only if stated
    currency: str | None  # overrides the Quotation currency if stated per line
    discount_pct: float | None
    quantity: float | None  # enables line-total cross-check
    line_total: float | None
    price_tiers: list[PriceTier]
    revision_note: str | None  # e.g. "corrected from 0.128 to 0.134 in P.S."
    evidence: list[LineFieldEvidence]  # {field, span, certainty} per non-null field


class Quotation(BaseModel):
    document_type: str  # quotation, price_list, order_confirmation, ...
    supplier: str | None
    quote_ref: str | None
    rfq_ref: str | None
    issue_date: date | None
    valid_until: date | None
    currency: str | None
    incoterm: str | None
    incoterm_place: str | None
    lines: list[QuotationLine]
    evidence: list[QuotationFieldEvidence]  # same shape, for the terms above
```

Evidence and certainty were first sketched as `dict[field, ...]` maps. They are a list of `{field, span, certainty}` entries instead: Gemini's structured output can't reliably express open-ended dict keys, and an enum of field names stops the model inventing fields. Quotation-level terms carry evidence too, so the currency or supplier can be checked like line values.

| Change vs the brief's schema | Why the original falls short |
|---|---|
| Quotation header + **list** of lines | A quotation covers several products, and terms apply to all lines |
| `active_ingredients` list | Combination products (TLD, HRZE, Amox+Clav) need one entry per ingredient. Normalized INNs match across suppliers ("amoxicillin trihydrate" = "amoxicillin") |
| `dosage_form` | Same INN in different forms is a different product (salbutamol inhaler ≠ syrup) |
| Stated `unit_price` / `pack_price` instead of `price_per_uom` (ADR-0002) | Documents quote per tablet, per box or per ampoule. Code derives the other price and cross-checks |
| `price_tiers`, `discount_pct` | Volume tiers and per-line discounts are common commercial shapes |
| `incoterm` | Prices under different Incoterms aren't like-for-like |
| `evidence`, `certainty`, `revision_note` | Reviewability and uncertainty signals |

**Prompt contract:**
- Field instructions live in the Pydantic field descriptions.
- A short rule block: final values after corrections, record revisions in `revision_note`, `null` when unreadable, never compute a value the document doesn't state.
- No few-shot examples from the pack.

**Out of scope:**
- **Alternative offers** mentioned in notes (e.g. "a 100 mL presentation is available at …") aren't extracted.
- **Non-quotation documents** are recorded as `skipped_unsupported_type`.

---

## 6. Validation, normalization and review (ADR-0004)

**Derived values** are stored next to the originals, never in their place:
- the headline Unit and Pack price: as stated on the line, else in the lowest-volume tier (smallest `min_qty`, not the first listed), with the missing one calculated from the other and the Pack size. Any line with tiers gets `TIERED_PRICING`, since its price depends on volume. A Pack size or discount the rules reject calculates and cross-checks nothing;
- the net-of-discount headline prices, empty when no discount is stated;
- USD per UOM: the headline Unit price before discount.

**FX:**
- Rates come from Frankfurter (free, ECB reference rates), on the **quotation issue date**.
- Fetched rates are cached in a SQLite `fx_rates` table.
- Rate, rate date and source are stored with each conversion. The rate date can precede the issue date: for a weekend or holiday the ECB's last published rate is used.
- With no rate (API down, currency outside the ECB set, no issue date), the USD price is null with reason `FX_UNAVAILABLE`, never estimated. Missing rates aren't cached, so a re-run retries.
- A USD price is stored with rate 1 and no rate date or source.

**Per-field verification:**

| Result | When |
|---|---|
| verified | evidence span found verbatim in source text, or an arithmetic cross-check passes, or two reads agree |
| unverified | no check possible, nothing contradicts the value |
| failed | contradicted by a check, null or unreadable, or rule broken (invalid ISO currency, price ≤ 0) |

**Checks:**
- **Evidence check** (text sources). Span and text are compared after normalizing whitespace, quote marks, case and number formatting (`1,250.00` = `1250` = `1.250,00`); numbers are matched whole, so `25` never matches inside `1250`. A real span can still back a wrong number, so a numeric value must also be written in its own span. Numbers inside list entries (strengths, tier rows) need only be written somewhere in the Source: the model gives one span per field, which rarely covers every entry. That still catches computed values, e.g. tier quantities converted from packs to units.
- **Arithmetic checks**, within 1% relative tolerance: enough for a Unit price rounded to the cent (12.50 / 30 written as 0.42 is 0.8% off), too little for a price on the wrong quantity. A pass verifies every field involved; a mismatch fails them all with `ARITHMETIC_MISMATCH`, since arithmetic shows that a value is wrong, not which.
  - unit × pack_size ≈ pack_price, when all three are stated, on the line and on each Price tier.
  - qty × price × (1 − discount) ≈ line_total. Documents count the quantity in UOMs or in Packs without saying which, so either price, stated or calculated, may produce the total; a mismatch also fails the Pack size when a price calculated from it was tried. Tier-only lines are skipped: the tier that applies depends on the quantity.
- **Rule checks.** ISO 4217 currency (`INVALID_CURRENCY`), price > 0 (`NON_POSITIVE_PRICE`), critical field present (`MISSING_CRITICAL_FIELD`), and plausible magnitude (`IMPLAUSIBLE_VALUE`: pack size a positive whole number, discount within 0–100%, positive quantity, line total and strength).
- **Second-read agreement.** A conditional edge sends image Sources, and Sources with a line whose critical checks failed, to a second extractor with a stronger model. On an image every line is compared; on a text Source only the failed lines are, since the rest are already verified against the text. A field both reads give alike is verified; one they give differently fails with `SECOND_READ_DISAGREEMENT`. Strings are compared ignoring case and punctuation, ingredients by INN and strength. Three rules came from the sample run:
  - A value one read leaves empty confirms nothing and contradicts nothing, as with nulls across Sources. A glare-hidden price stays missing; it doesn't take the other read's guess.
  - Lines pair by position. Reads with different line counts (one read split a row), or that list other active ingredients in the same position (a reordered read), compare no line and say so: position pairing would then compare the wrong products. Pairing by the full Product key, as reconciliation does, was rejected: it includes the strength, so a misread strength would leave the lines unpaired, and that's a misread the second read exists to catch.
  - The dosage form isn't compared. It's a standardized term the model chooses ("Syrup" or "Oral syrup"), not a value read off the page.
  - A second read that fails (a quota error, or malformed output on every retry) leaves the first read standing, with the note `SECOND_READ_UNAVAILABLE`. It only adds confidence, so losing it mustn't lose the Source; an image line it would have verified stays in review via `UNVERIFIED_IMAGE_VALUE`.

**`needs_review` when:**
- a critical field (price, currency, pack size, UOM, ingredients/strength) is failed, or is unverified on an image Source (`UNVERIFIED_IMAGE_VALUE`). An image Source is one the model read as images: a photo or a scanned PDF;
- the line is in a Conflict;
- the line has a `revision_note`.

`certainty: inferred` adds a review reason (`INFERRED_VALUE`) but doesn't force review on its own. `certainty: unclear` means partly unreadable, so the field fails (`UNCLEAR_VALUE`), like a null.

**Review reasons** are stable codes plus messages, e.g. `{code: "EVIDENCE_NOT_FOUND", field: "unit_price", message: "0.134 not found in source text"}`. The UI filters by code and the eval counts them. Reasons are rows in a `review_reasons` table with an unconstrained `code` column, so a new code needs no schema change.

---

## 7. Images

- Direct vision extraction with structured output. No OCR engine or preprocessing.
- The prompt requires `null` for unreadable values. Glare-hidden prices must come back null, never as values guessed from context.
- The arithmetic checks are what make image lines verifiable. Where a document carries line totals or subtotals, an image line can be auto-accepted.
- A second read with a stronger Gemini model flags fields where the two reads disagree.

Both pack images are scans of the Andina PDF, so expected values come from the clean PDF. Expected outcome:
- **`scan_02`** (skewed, noisy, columns visually misaligned) mostly verifies via the line totals.
- **`scan_03`** items 01–03 (unit prices under glare) go to review.

**Result** (flash-lite extracts, flash second-reads; re-run with flash-lite second reads in `eval/results.md`, same outcome for `scan_02`):
- **`scan_02`**: 3 of 6 lines `auto_ok`, every field verified by the line total and agreeing reads. Line 1 has a misread unit price (0.0094 for 0.0091). Both reads made the same misread, so agreement couldn't catch it; the line-total check and the Conflict with the PDF did. Lines 4 and 6 state no pack size, as in the clean PDF, which sends those lines to review too.
- **`scan_03`**: the three glare-hidden unit prices come back empty and those lines are `needs_review`. On the first run the first read split one row into two (7 lines against 6), so the reads weren't compared and every line was in review. On the committed re-run both reads give 6 lines; all 6 are still in review, on missing prices, line totals that contradict their own spans (`41` read from `41,400.00`) and arithmetic mismatches.

How to read the `scan_02` number: the clean PDF itself can auto-accept only 4 of the 6 lines, since 2 state no pack size. Of those 4, the scan auto-accepts 3 and correctly flags the 4th as a misread. Accepting that line would be the actual failure. The prompt wasn't tuned to raise the number: that would fit it to the sample (ADR-0001).

Two reads from one provider made the same misread on `scan_02`, which is the correlated-error risk ADR-0004 names. The arithmetic checks are the stronger signal. For the same reason, agreeing reads show the reads match, not that they're right: two reads that both guessed a hidden value would verify it. The prompt rule against guessing is the only guard, so a line total, where present, matters more than agreement.

Line detail gives the Source to show for an image line, the image file or the scanned PDF.

**Write-up note:** a dedicated OCR engine would give per-word confidence. That was traded for simplicity, and second-read disagreement is the signal instead. Both reads come from one provider, so their errors are less independent.

---

## 8. Reconciliation (ADR-0003)

**Conflicts:**
- Sources are linked to the same Quotation by supplier + quote reference, both normalized: case, accents and punctuation ignored, legal forms (Ltd, S.A., GmbH, ...) dropped from the supplier. In the reference only the separator characters are ignored, not where they fall, so "SP-77" = "sp 77" but "Q1-23" ≠ "Q12-3". A Source with no readable reference stays its own Quotation, with reason `NO_QUOTE_REFERENCE`; one with no supplier likewise, with `NO_SUPPLIER`, since a shared reference alone could belong to two suppliers. Both reasons are notes and don't force review.
- Lines are matched within a Quotation by Product key (INN + strength set, plus dosage form). When a Source quotes one Product more than once (e.g. two pack sizes), its lines for that Product pair by pack size, then by document order among lines with the same or no pack size. A Product quoted once per Source is matched whatever its pack size, so a misread pack size still shows as a Conflict. A line some linked Source has no partner for (e.g. one copy couldn't read a strength, so its Product key differs) gets the note `UNMATCHED_IN_QUOTATION` naming that Source. It doesn't force review, because a partial copy legitimately lacks lines, but a Conflict can no longer go missing silently.
- Matched lines are compared on brand, UOM, pack size, prices, currency, discount, quantity, line total and tiers. A field stated differently becomes a Conflict in `conflicts` / `conflict_values`, with each Source's value, and those lines go to review with `IN_CONFLICT`. A value one Source leaves null (e.g. glare-hidden) isn't a disagreement: verification already flags it, and counting it would push the clean copy into review too.
- Reconciliation runs at the end of every `ingest` over all stored Sources and replaces its earlier outcome. Lines keep their own `verification_status` next to `review_status`, so a re-ingested Source can't leave stale flags on lines it used to disagree with.
- Quotation terms are compared across linked Sources too: Incoterm and its place, issue date, validity and RFQ reference, with the same normalization and null rule. Currency is already compared as each line's effective currency. A term applies to every line of its Source, so a differing one puts all lines of each Source that states it in review with `IN_CONFLICT` on that term: a different Incoterm or issue date changes what every price means. The Conflict is stored against each of those lines, with `scope = 'quotation'`, and the read model gives one value per Source. Quotation-scoped Conflict rows were rejected for a POC: `conflict_values.line_id` is `NOT NULL` in existing databases, and per-line rows reuse the path that already puts a line in a Conflict into review. A Source with no lines has nothing to review, so its terms aren't compared.
- A placeholder reference (no digit, e.g. "N/A") counts as missing.
- In the pack, the Andina PDF and its two scans are three Sources of one Quotation.

**Price comparison:**
- Groups lines from different Quotations by INN set + strengths + standardized dosage form, all exact. It uses the Product key Conflicts match on. A group needs lines from at least two Quotations. A Source that can't be linked counts as a Quotation of its own.
- The key drops trailing salt and hydrate words from the INN ("tenofovir disoproxil fumarate" = "tenofovir disoproxil"). The schema asks for the INN without its salt, but on the sample run the model left one on, and the TLD triple didn't group. A name made only of salt words ("magnesium sulfate") stays whole, so it never folds into another salt of the same cation. The stored INN stays as extracted.
- A line with an unreadable INN, strength or dosage form isn't compared. Two lines missing the same part would share a key without being known to be the same Product.
- Shows USD per UOM (before discount, tier 1) with each line's Incoterm and place. When the Incoterms differ it warns "not like-for-like", naming each Quotation's term. A Quotation that states no Incoterm also triggers the warning, since what its price includes is unknown. A different place under the same Incoterm doesn't trigger it.
- Groups are stored next to Conflicts and replaced on every run. A comparison never adds a Review reason or changes a review status.

Overlaps in the pack:

| Product | Quotations |
|---|---|
| TLD 300/300/50 | Sanova, Mekong |
| Amlodipine 5 mg | Sanova, Andina |
| Amox/Clav 500/125 | Zenith, Mekong |
| Paracetamol 500 mg | Ubuntu, Andina |

Salbutamol (inhaler vs syrup) must stay separate.

**Result:** all four overlaps form groups, and each carries the Incoterm warning (FCA vs CIF, FOB vs FCA, CIF vs CIP, FOB vs EXW). The salbutamol inhaler and syrup stay apart. Four more groups come from the Andina family only, because glare made `scan_03` read the reference as `FA-COT-2026-158` (`FA-COT-2636-108` on the committed re-run). That Source is unlinked, so the system can't tell it's the same Quotation. Matching references fuzzily was rejected (ADR-0003). The group shows one supplier quoting identical prices under two references, which a reviewer can spot. The exact dosage form also costs a pair: `scan_02` read the amoxicillin suspension as "Oral suspension powder", the PDF as "Powder for oral suspension".

Reconciliation never merges or picks a winner.

---

## 9. Output and review UI

**SQLite is the source of truth:**
- `documents`: one row per Source, with processing status.
- `quotations`
- `quotation_lines`: originals, derived values, verification, reasons and evidence.
- `conflicts`
- `fx_rates`
- `review_decisions`: who, when, field, old → new, approve/correct. Extracted values are never overwritten.

**Review decisions:**
- The current value of a field is its latest correction, else the extracted value. The read model returns current values and keeps each corrected field's extracted value next to it. Headline, net and USD prices are recalculated from current values, using only FX rates ingest already cached, so a correction never fetches or estimates a rate.
- A line's review status is `reviewed` when its latest decision is an approval, otherwise the pipeline's `needs_review` or `auto_ok`. One column then answers whether a line still needs a human, and reviewed lines leave the `needs_review` queue and count. It's worked out on read, not stored: reconciliation rewrites the stored status on every ingest.
- A later correction withdraws an approval, since the approval covered other values. So does a Review reason raised after it, e.g. a newly ingested Source of the same Quotation that conflicts with the line: an approval records the reasons the reviewer saw, by code and field, and the line goes back to the pipeline's status when one appears that wasn't among them.
- Review reasons stay what verification and reconciliation found in the Sources; a correction or approval doesn't clear them, so a reviewed line still shows why it was flagged. The one reason a correction adds is `FX_UNAVAILABLE`, when a corrected currency has no cached rate.
- A line currency corrected to empty falls back to the Quotation's, as an extracted one does.
- A decision applies to the reading of the Source it was made on. A re-run leaves an unchanged Source alone (§2), so its decisions stand. A Source read again (new content, models or prompt, or `--force`) starts a fresh review: its earlier decisions stay in the log, marked as no longer current, but aren't applied. Carrying them over was rejected: a new reading can drop, split or reorder lines, so a decision matched by line number could land on another Product, and the values a reviewer approved may no longer be the ones shown.
- Reconciliation compares extracted values. A correction resolves the line for export; it doesn't make the Sources agree, so the Conflict stays visible.
- **CSV export** (`sdi export <file.csv>`): one row per line with current values, derived prices, review status, corrected fields, Review reasons, and Source path + line number.

**Known limitation:** reprocessing after a pipeline change means reviewing those Sources again. Production could offer earlier decisions as suggestions where the new reading's line has the same Product and extracted value, for a reviewer to confirm.

**Streamlit UI** (`app/review.py`; POC, production would be an API service + a proper frontend). It reads only through the read model and writes only `Store.approve` / `Store.correct`, so it holds no business logic and has no unit tests:
1. **Review queue:** `needs_review` first, filter by supplier, status, Source and reason code; reasons as tags. Selecting a row opens it.
2. **Line detail:** original and calculated values with FX details, each field's verification and evidence, and the source image for image lines. The image comes from the same loader the pipeline used, so it's what the model saw. Approve, or correct a field; the decision history sits below.
3. **Conflicts:** each Source's value side by side.
4. **Price comparison**, with Incoterm warnings.
5. **Source status:** processing status and errors.
6. **CSV export**, the same file as `sdi export`.

The reviewer types their name in the sidebar; there is no login. Production would take it from authentication.

Source paths are stored as `sdi ingest` was given them, so the UI shows source images when launched from the same folder, and says so when it can't find the file. Production would keep Sources in object storage under a stable key.

---

## 10. Framework and model (ADR-0005)

**LangGraph:**
- **Nodes:** one per pipeline step.
- **Conditional edge:** for the second read.
- **Retries:** on schema-validation failure, via `with_structured_output` + Pydantic.
- **Tracing:** LangSmith (free tier).

Costs to record for the write-up: state-typing boilerplate, overkill for a mostly linear flow, and abstraction leaks.

**Model:** Gemini free tier via `langchain-google-genai`, a lighter model for extraction and a stronger one for the second read. Current model names and free-tier limits get checked at setup. During development a LangChain SQLite LLM cache keeps re-runs free and the eval reproducible.

---

## 11. Evaluation

- **Gold set:** `eval/gold/<source>.json`, one hand-labelled file per Source. Drafted by the agent, verified by the author. Includes expected nulls for glare-hidden scan values; `any_of` accepts a partly visible value read either way; a field left out isn't scored.
- **Command:** `sdi eval data samples` ingests into a fresh temporary database every run, so a change to validation can't be hidden by ingest skipping unchanged Sources. `--cached-only` turns a cache miss into an unavailable model instead of a call, so a re-run is free and its numbers identical. Results go to `eval/results.md` and `.json`. It reports, per Source type:
  1. **Line matching:** expected lines found, plus extra lines.
  2. **Field accuracy** on matched lines: numbers within 0.5%, strings normalized.
  3. **Flagging quality:** share of wrong fields that were flagged.
  4. **Review load:** % lines `needs_review`, % fields flagged, flags on correct fields.

---

## 12. Testing and repo

- **Tests:** plain pytest for the deterministic parts (loaders, checks, FX with stubbed HTTP, reconciliation, review rules), with no LLM calls. LLM behavior is covered by the cached eval. CI (ruff + pytest + cached eval with a score threshold) is described in the write-up, not built.
- **Tooling:** `uv`, Python 3.12, ruff, pytest.

```
src/sdi/
  loaders/        registry + pdf, json, email, image, excel
  extraction/     schema.py, prompt.py, gemini.py (the extractor)
  validation/     checks + review reasons
  reconcile/      conflicts + price comparison
  pipeline.py     LangGraph graph per Source + ingest()
  store.py        SQLite writes
  read_model.py   every query the CLI and UI use
  evaluation.py   eval scoring against the gold set
  fx.py  config.py  cli.py
app/review.py     Streamlit UI
eval/gold/  eval/results.md
docs/WRITEUP.md  docs/agent-session-summary.md  docs/adr/
```

**Run:**
- `uv run sdi ingest data/`
- `uv run sdi export lines.csv`
- `uv run sdi eval data samples --cached-only`
- `uv run streamlit run app/review.py`

**Secrets:** `.env` holds `GOOGLE_API_KEY`, with a committed `.env.example`.

---

## 13. Time budget

| Time | Work |
|---|---|
| 0:00–0:20 | `uv` project, Gemini key setup, `.env.example` |
| 0:20–1:00 | Pydantic schema, `DocumentContent`, loader registry + loaders |
| 1:00–2:00 | LangGraph graph + structured output, with JSON and PDF working end to end |
| 2:00–2:45 | Email, images, second-read edge |
| 2:45–3:30 | Checks, derived prices, FX, verification, review reasons |
| 3:30–4:00 | Reconciliation, SQLite |
| 4:00–4:30 | Gold set + eval; Excel loader as an isolated commit |
| 4:30–5:00 | Streamlit review UI |
| 5:00–5:45 | Write-up, README, agent session summary, video |

**Cut first if behind:**
1. Second read (keep the arithmetic checks).
2. UI correct-field action (keep approve).
3. Price comparison view (keep Conflicts).

**Never cut:** evidence and arithmetic checks, eval set, Excel extension commit, write-up.

---

## 14. Write-up coverage map

| Required section | Source |
|---|---|
| Architecture and decisions | §1, §2, ADRs |
| Extensibility | §1, §3, §4 (Excel commit as proof) |
| Framework choice and cost | §10 |
| Natural-language and image vs structured sources | §3 (email loader), §5 (`revision_note`), §7 |
| Uncertainty signalling | §6, §7, ADR-0004 |
| Production readiness | Column-mapping cache, provider-agnostic models with fallbacks, paid or no-training tier for PII, FastAPI review app, tracing |
| CI/CD and infrastructure | Cached eval as regression gate, containerized worker, queue-based scaling |
| Limitations | Reprocessing a Source restarts its review, whole-table serialization, no OCR confidence, single-provider second read (correlated misreads), mixed text/scanned PDFs read as text, alternative offers not extracted, exact dosage-form matching, salt words stripped from a fixed list |
| Data model reflection | §5 table |
