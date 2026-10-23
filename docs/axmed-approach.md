# Axmed Take-Home — Proposed Approach

Document Intelligence Agent: ingest heterogeneous supplier documents, extract quotation lines into a common schema, flag uncertainty, and hand results to a human reviewer.

**Time-box:** 4–6 hours (target ~5.5h including deliverables).
**Constraints:** Python, a framework (LangGraph), Anthropic API key only.
**Most heavily weighted criterion:** generalisation to new formats, suppliers and source systems.

---

## 1. Core principle: no per-supplier logic anywhere

Two kinds of "new" can arrive, and they are handled in different places:

| What arrives | Where it's handled | Code change |
|---|---|---|
| **New file type** (e.g. first Excel file ever) | Loader layer | One new loader function + one registry line |
| **New layout of a known type** (PDF laid out differently, JSON with unseen field names, Excel with different columns) | Generic LLM extractor | None |
| **Same product from two sources that disagree** | Reconciliation step | None. Flagged for review, never auto-merged |

- **Loaders handle format.** One per file type, written once.
- **The extractor handles layout and meaning.** Generic, so no supplier names, no layout hints, no filename branching.
- **Deterministic code handles arithmetic and rules.** Validation, unit price, currency conversion, conflict detection.

An unknown file extension fails loudly ("no loader for .xyz") rather than silently.

---

## 2. Pipeline overview

One file goes in; zero or more quotation lines (one per product) come out, each with review flags.

```
 one file (PDF / JSON / email / image / xlsx)
        │
   1. LOAD        loader picked by file type → DocumentContent (text and/or images)
        │
   2. EXTRACT     LLM fills the schema → list[QuotationLine]
        │
   3. VALIDATE    evidence check, rule checks, price per unit, currency conversion,
   & NORMALIZE    review_status + review_reasons
        │
   4. STORE       lines written to SQLite
        │
 ── after all files are processed ──
        │
   5. RECONCILE   group same product across documents, compare normalized prices,
                  write disagreements to a conflicts table
        │
   6. REVIEW      human reviews needs_review rows and conflicts first
```

Steps 1–4 run **per document**. Step 5 runs **across documents**, because only it needs more than one document.

---

## 3. DocumentContent: the loader output

`DocumentContent` is the **input** to extraction, not its output. It's the common intermediate format that lets the extractor ignore where content came from.

```python
class DocumentContent(BaseModel):
    source_path: str
    text: str | None        # anything readable as text
    images: list[bytes]     # anything that must be *seen* (photos, rendered scanned pages)
    metadata: dict          # file type, page count, etc.
```

| Source | `text` | `images` |
|---|---|---|
| Clean PDF | extracted text (pdfplumber) | `[]` |
| Scanned PDF (near-zero text yield) | `None` | each page rendered to PNG |
| Photo / scan (.jpg, .png) | `None` | `[the image]` |
| JSON / email / Excel | serialized content | `[]` |

The scanned-PDF fallback is generic (triggered by low text yield), not a special case for a known file.

### Loader registry

```python
# loaders/excel.py
@register(".xlsx", ".xls")
def load_excel(path) -> DocumentContent:
    sheets = pd.read_excel(path, sheet_name=None)
    text = "\n\n".join(f"## {n}\n{df.to_markdown()}" for n, df in sheets.items())
    return DocumentContent(source_path=str(path), text=text, images=[])
```

The pipeline calls `registry[file.suffix]`. Nothing downstream changes when a loader is added.

---

## 4. Extensibility proof: the Excel loader

The pack contains no Excel files, so Excel represents a genuinely **new file type**.

- Build the Excel loader **after** the pipeline works, in **one isolated commit**.
- That commit's diff should touch only `loaders/excel.py` and a synthetic `samples/price_list.xlsx`. It should change nothing in the extractor, graph, validation or output.
- The write-up links that commit as proof: "a new file type required one new file."
- The synthetic Excel copies rows from one of the PDF quotes, which also gives a free cross-check (extracted values should match).
- If adding it forces changes elsewhere, that reveals a design leak worth fixing before submission.

### Known limitation (stated in the write-up)

Serializing tables to text is fine for small files: LLMs read markdown tables well, and numbers are copied as written. It breaks down for large sheets, where token cost grows, rows can be silently dropped, and merged cells or multi-row headers get flattened.

**Production path for tabular sources (JSON, Excel):** send headers plus a few sample rows. The LLM returns a **column mapping** (e.g. `"Precio/caja" → pack_price`), which is cached per structure fingerprint and applied to all rows deterministically. That means one LLM call per new layout instead of per row, no row loss, and an inspectable mapping. Build for JSON only if ahead of schedule; otherwise describe it.

---

## 5. Extraction schema

The brief's 5-field schema is a suggested starting point. It becomes a Pydantic model with these extensions, each of which answers the write-up question on data-model shortcomings:

```python
class QuotationLine(BaseModel):
    product_name: str
    inn: str | None             # generic name of the active ingredient
    strength: str | None        # amount of active ingredient per unit: "500 mg", "250 mg/5 mL"
    dosage_form: str | None     # tablet, capsule, oral suspension, injection...
    supplier: str | None
    pack_size: int | None
    uom: str | None
    pack_price: float | None
    currency: str | None
    evidence: dict[str, str]    # field → verbatim source span it was read from
    confidence: dict[str, float]  # self-reported; weakest signal
    revision_note: str | None   # e.g. "price corrected from 4.20 to 3.90 later in thread"

class ExtractionResult(BaseModel):
    lines: list[QuotationLine]
```

| Change | Why the original schema falls short |
|---|---|
| Document → **list** of lines | A quotation usually covers several products |
| `strength`, `dosage_form` | INN alone doesn't identify a medicine. Amoxicillin 500 mg capsule ≠ 250 mg/5 mL suspension ≠ 1 g injection, so they must not be grouped or price-compared |
| `pack_price` + `pack_size` + `uom` instead of `price_per_uom` | Suppliers often quote per pack; unit price is computed in code, not by the LLM |
| `supplier` | Needed to compare the same product across suppliers |
| `evidence`, `confidence`, `revision_note` | Uncertainty signalling and reviewability |

`revision_note` exists for **every** source and stays `null` for PDF/JSON. It gets filled for the email case: the prompt asks for **final agreed values**, and the field records that a correction occurred, so the reviewer can verify the model chose the right value rather than having the revision silently resolved.

---

## 6. Validation & normalization (step 3, per line)

Everything here depends on a single line only, so it happens before storage.

**Derived columns are stored next to the originals:**

| pack_price | currency | pack_size | price_per_unit | price_per_unit_usd |
|---|---|---|---|---|
| 2.10 | EUR | 20 | 0.105 | 0.114 |

- Originals = what the document says, which is what the reviewer verifies.
- Converted values depend on a **hard-coded, dated FX table** (a stated assumption). They are recomputable without re-extraction.

**Uncertainty signals, strongest first:**

1. **Evidence check (text sources).** The verbatim span returned by the model must exist in the source text. If it's missing, the value may be hallucinated, so the line is flagged.
2. **Rule validation.** Valid ISO currency, positive price, pack size present, plausible unit price.
3. **Self-reported confidence.** Kept, but treated as weakest. LLM verbalized confidence is empirically poorly calibrated.

**Output per line:** `review_status` (`auto_ok` / `needs_review`) plus `review_reasons` in plain language, e.g. "price not found verbatim in source", "currency inferred, not stated".

---

## 7. Images (Anthropic-only, no OCR)

- Images go **directly to a Claude Haiku-tier vision model** with structured output. No Tesseract, no preprocessing.
- The evidence check has no source text to verify against, so image-derived lines:
  - go through the same rule validation;
  - start as `needs_review` unless every check passes (honest lower trust beats fake calibration);
  - **optional:** if validation fails, re-run with a Sonnet-tier model. Fields where the two models disagree get flagged. This is one conditional edge in the graph, using the same API key.
- Write-up note: a dedicated OCR engine would give per-word confidence. That was traded for simplicity, with cross-model disagreement used as the uncertainty signal instead.

---

## 8. Reconciliation (step 5, across documents)

Prices are already normalized in step 3, so reconciliation only does what genuinely needs multiple documents:

1. **Group** lines describing the same product: normalized INN + strength + dosage form.
2. **Compare** normalized unit prices (and units) within each group.
3. **Flag** meaningful disagreements in a `conflicts` table. Examples: large price gap, unclear units, the same supplier quoting two prices (e.g. a PDF quote later revised by email).

It never picks a winner or merges. The reviewer sees the related rows side by side.

Example: Supplier A's JSON (amoxicillin 500 mg, box of 100, USD 12.00 → $0.12/unit) vs Supplier B's PDF (pack of 20, EUR 2.10 → ~$0.11/unit). Both are valid; they are comparable only after normalization.

---

## 9. Output

SQLite, plus a CSV export:

- **documents**: one row per file (source, type, processing status)
- **quotation_lines**: one row per product per document, with originals, derived columns, flags and evidence
- **conflicts**: links between lines for the same product that disagree

---

## 10. Framework & model

**LangGraph**
- Nodes map onto the pipeline; conditional edges cover the Sonnet escalation.
- `with_structured_output` + Pydantic, built-in retries, LangSmith tracing (free tier).
- Costs to record as they appear (for the write-up): state-typing boilerplate, overkill for a mostly linear flow, abstraction leaks for provider-specific features.

**Model:** Claude Haiku-tier by default; Sonnet-tier only on validation failure. Swappable via config (`init_chat_model`). Check current model names and pricing before starting.

---

## 11. Evaluation

- Hand-label expected values for the provided documents in `gold.json`.
- A script reports per-field accuracy, giving real numbers for the write-up (~30 min).

---

## 12. Time budget

| Time | Work |
|---|---|
| 0:00–0:20 | Repo, `uv`, `CLAUDE.md` (commit at every meaningful step, messages explain *why*), inspect the pack |
| 0:20–1:00 | Pydantic schema, `DocumentContent`, loader registry |
| 1:00–2:00 | LangGraph pipeline + structured output; JSON and PDF end to end |
| 2:00–2:45 | Email, images, scanned-PDF fallback |
| 2:45–3:30 | Validation, evidence check, unit price + FX, `review_reasons` |
| 3:30–4:00 | Reconciliation + conflicts table, SQLite/CSV output |
| 4:00–4:30 | Gold set + eval script; Excel loader as isolated extension commit |
| 4:30–5:30 | Write-up, README, agent-generated summary `.md`, 5-min video |

**Cut first if behind:** Sonnet escalation → JSON column-mapping cache → reconciliation depth (keep grouping + flagging).
**Never cut:** evidence check, eval set, Excel extension commit, write-up.

---

## 13. Write-up coverage map

| Required section | Source in this plan |
|---|---|
| Architecture & decisions | §1, §2 |
| Extensibility | §1, §3, §4 (Excel commit as proof) |
| Framework choice & cost | §10 |
| NL & image vs structured | §5 (`revision_note`), §7 |
| Uncertainty signalling | §6, §7 |
| Production readiness | Column-mapping cache (§4), model tiering, tracing, PII handling, review UI |
| CI/CD & infra | Eval set as regression test in CI, containerized worker, queue-based scaling |
| Limitations | Text serialization of tables (§4), no OCR confidence (§7), static FX table (§6) |
| Data model reflection | §5 table |
