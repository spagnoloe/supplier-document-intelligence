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

---

## 3. DocumentContent: the loader output

```python
class DocumentContent(BaseModel):
    source_path: str
    text: str | None        # anything readable as text
    images: list[bytes]     # anything that must be seen
    metadata: dict          # file type, page count, ...
```

| Source | `text` | `images` |
|---|---|---|
| Text PDF | extracted text (pdfplumber) | `[]` |
| Scanned PDF (near-zero text yield) | `None` | each page rendered to PNG |
| Photo / scan | `None` | `[the image]` |
| JSON / Excel | serialized content | `[]` |
| Email (.eml) | From/Date/Subject headers + text/plain body (HTML→text fallback) | `[]` |

The scanned-PDF fallback is generic (low text yield). The pack has no scanned PDFs, so it's included but not exercised by the sample.

---

## 4. Extensibility proof: the Excel loader

- Built **after** the pipeline works, in **one isolated commit** (ideally its own PR).
- That diff touches only `loaders/excel.py` and a synthetic `samples/price_list.xlsx`, with rows copied from a PDF quote so the extracted values cross-check.
- If it forces changes elsewhere, that's a design leak to fix before submission.

**Known limitation:** tables are serialized whole into the prompt. That's fine for small files. Large sheets cost tokens and risk dropped rows. The production path is an LLM-proposed column mapping cached per structure fingerprint. It's described, not built.

---

## 5. Extraction schema

Quotation-level terms are separate from lines, so the LLM can't contradict itself across lines.

```python
class ActiveIngredient(BaseModel):
    name_as_written: str        # evidence-checked
    inn: str                    # WHO INN of the active moiety, salt stripped
    strength_value: float | None
    strength_unit: str | None   # "mg", "mg/5 mL", "IU/mL", "mcg/actuation"

class PriceTier(BaseModel):
    min_qty: float | None
    max_qty: float | None
    unit_price: float | None
    pack_price: float | None

class QuotationLine(BaseModel):
    brand_name: str | None
    active_ingredients: list[ActiveIngredient]
    dosage_form: str | None     # standardized term, e.g. "Film-coated tablet"
    uom: str | None             # tablet, vial, ampoule, bottle, pen ...
    pack_size: float | None     # UOMs per Pack
    unit_price: float | None    # only if stated
    pack_price: float | None    # only if stated
    currency: str | None        # overrides the Quotation currency if stated per line
    discount_pct: float | None
    quantity: float | None      # enables line-total cross-check
    line_total: float | None
    price_tiers: list[PriceTier]
    revision_note: str | None   # e.g. "corrected from 0.128 to 0.134 in P.S."
    evidence: dict[str, str]    # field → verbatim source span
    certainty: dict[str, Literal["stated", "inferred", "unclear"]]

class Quotation(BaseModel):
    document_type: str          # quotation, price_list, order_confirmation, ...
    supplier: str | None
    quote_ref: str | None
    rfq_ref: str | None
    issue_date: date | None
    valid_until: date | None
    currency: str | None
    incoterm: str | None
    incoterm_place: str | None
    lines: list[QuotationLine]
```

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

**Derived values** are stored next to the originals:
- the missing unit or pack price;
- the net-of-discount price;
- tier 1 as the headline price;
- USD per UOM.

**FX:**
- Rates come from Frankfurter (free, ECB reference rates), on the **quotation issue date**.
- Fetched rates are cached in a SQLite `fx_rates` table.
- Rate, rate date and source are stored with each conversion.
- If the API is unavailable, the USD price is null with reason `FX_UNAVAILABLE`.

**Per-field verification:**

| Result | When |
|---|---|
| verified | evidence span found verbatim in source text, or an arithmetic cross-check passes, or two image reads agree |
| unverified | no check possible, nothing contradicts the value |
| failed | contradicted by a check, null or unreadable, or rule broken (invalid ISO currency, price ≤ 0) |

**Checks:**
- **Evidence check** (text sources).
- **Arithmetic checks**, within tolerance:
  - unit × pack_size ≈ pack_price
  - qty × unit × (1 − discount) ≈ line_total
- **Rule checks.**
- **Second-read agreement** (images and failed lines).

**`needs_review` when:**
- a critical field (price, currency, pack size, UOM, ingredients/strength) is failed, or is unverified on an image Source;
- the line is in a Conflict;
- the line has a `revision_note`.

`certainty: inferred` adds a review reason but doesn't force review on its own.

**Review reasons** are stable codes plus messages, e.g. `{code: "EVIDENCE_NOT_FOUND", field: "unit_price", message: "0.134 not found in source text"}`. The UI filters by code and the eval counts them.

---

## 7. Images

- Direct vision extraction with structured output. No OCR engine or preprocessing.
- The prompt requires `null` for unreadable values. Glare-hidden prices must come back null, never as values guessed from context.
- The arithmetic checks are what make image lines verifiable. Where a document carries line totals or subtotals, an image line can be auto-accepted.
- A second read with a stronger Gemini model flags fields where the two reads disagree.

Both pack images are scans of the Andina PDF, so expected values come from the clean PDF. Expected outcome:
- **`scan_02`** (skewed, noisy, columns visually misaligned) mostly verifies via the line totals.
- **`scan_03`** items 01–03 (unit prices under glare) go to review.

**Write-up note:** a dedicated OCR engine would give per-word confidence. That was traded for simplicity, and second-read disagreement is the signal instead. Both reads come from one provider, so their errors are less independent.

---

## 8. Reconciliation (ADR-0003)

**Conflicts:**
- Sources are linked to the same Quotation by supplier + quote reference. A Source with no readable reference stays its own Quotation, with reason `NO_QUOTE_REFERENCE`.
- Lines are matched within a Quotation. Disagreeing fields go into the `conflicts` table, and the lines go to review.
- In the pack, the Andina PDF and its two scans are three Sources of one Quotation.

**Price comparison:**
- Groups lines from different Quotations by INN set + strengths + standardized dosage form, all exact.
- Shows USD per UOM (before discount, tier 1) with the Incoterm, and warns when Incoterms differ.

Overlaps in the pack:

| Product | Quotations |
|---|---|
| TLD 300/300/50 | Sanova, Mekong |
| Amlodipine 5 mg | Sanova, Andina |
| Amox/Clav 500/125 | Zenith, Mekong |
| Paracetamol 500 mg | Ubuntu, Andina |

Salbutamol (inhaler vs syrup) must stay separate.

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

**Streamlit UI** (POC; production would be FastAPI + a proper frontend):
1. **Review queue:** `needs_review` first, filter by supplier, status, Source and reason code.
2. **Line detail:** fields next to their evidence, and the source image for image lines. Approve, or correct a field.
3. **Conflicts:** side by side.
4. **Price comparison**, with Incoterm warnings.
5. **CSV export.**

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

- **Gold set:** `eval/gold.json`, hand-labelled. Drafted by the agent, verified by the author. Includes expected nulls for glare-hidden scan values.
- **Script:** `eval/run_eval.py` reports, per Source type:
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
  loaders/        registry + pdf, json, email, image (later: excel)
  extraction/     schema.py, prompt, graph.py
  validation/     checks + review reasons
  reconcile/      conflicts + price comparison
  fx.py  store.py  cli.py
app/review.py     Streamlit UI
eval/gold.json  eval/run_eval.py
docs/WRITEUP.md  docs/agent-session-summary.md  docs/adr/
```

**Run:**
- `uv run sdi ingest data/`
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
| Limitations | Whole-table serialization, no OCR confidence, single-provider second read, alternative offers not extracted, exact dosage-form matching |
| Data model reflection | §5 table |
