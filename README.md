# Supplier document intelligence

Reads supplier quotations in any layout (PDF, JSON export, email, scan or photo, Excel), extracts their lines into one schema, checks each value against the document, and hands the result to a human reviewer.

- **Write-up:** [`docs/WRITEUP.md`](docs/WRITEUP.md) (one page). Design detail is in [`docs/approach.md`](docs/approach.md) and [`docs/adr/`](docs/adr/); terms are in [`GLOSSARY.md`](GLOSSARY.md).
- **Output table:** [`output/quotation_lines.csv`](output/quotation_lines.csv), one row per Quotation line from the sample pack in `data/`, with review status, Review reasons and the Source path + line number.
- **Eval:** [`eval/results.md`](eval/results.md); method in [`eval/README.md`](eval/README.md).
- **Agent session summary:** [`docs/agent-session-summary.md`](docs/agent-session-summary.md).

## Setup

Needs [uv](https://docs.astral.sh/uv/) (it installs Python 3.12 if missing) and a Gemini API key.

```sh
uv sync
cp .env.example .env
```

**Gemini key:** sign in at [Google AI Studio](https://aistudio.google.com/apikey), click **Create API key**, and paste it into `.env` as `GOOGLE_API_KEY=...`. The free tier needs no card. Free-tier inputs may be used by Google to improve its products, so use it only for synthetic data like this pack.

`.env` also sets the two models (`SDI_EXTRACTION_MODEL`, `SDI_SECOND_READ_MODEL`), the database path and the concurrency. The defaults work. The stronger second-read model has a small free daily quota (about 20 requests); if it runs out, set `SDI_SECOND_READ_MODEL` to the extraction model, as the committed eval did.

## Ingest

```sh
uv run sdi ingest data
```

Each Source in the folder is loaded by file type, extracted by Gemini, verified, and stored in `sdi.db` (SQLite). Then Sources of the same Quotation are reconciled into Conflicts and Price comparisons. The command prints one row per Source: its status, line count, and how many lines need review. A file type with no loader is recorded as failed (`no loader for .xyz`) and the rest of the folder still runs.

- **Re-runs skip unchanged Sources.** A Source is read again only if its content, the models, the prompt or the schema changed, or it failed last time. Model responses are also cached in `.cache/llm.db`, so a repeated request costs nothing.
- **`--force`** reads every Source again, e.g. after a change to validation code, which the skip check doesn't cover.
- **A Source read again starts a fresh review.** Its earlier approvals and corrections stay in the decision log, marked as no longer current, but aren't applied: a new reading can drop, split or reorder lines, so an old decision could land on the wrong product.

Ingest needs network access to [Frankfurter](https://frankfurter.dev) for ECB exchange rates. Without it, non-USD lines get no USD price and the Review reason `FX_UNAVAILABLE`. The LLM cache isn't committed, so the first ingest from a fresh clone calls Gemini: one request per Source, plus a second read for each scan and each text Source with a line whose critical checks failed.

## Review UI

```sh
uv run streamlit run app/review.py
```

Run it from the folder you ingested from, so it can find the Source images. Type your name in the sidebar, then:

- **Review queue:** `needs_review` lines first, filterable by supplier, status, Source and reason code.
- **Line detail:** each value with its evidence, verification result and Review reasons; calculated and USD prices; the image the model saw, for scans. **Approve** the line or **correct** a field. Extracted values are never overwritten.
- **Conflicts:** Sources of one Quotation that disagree, value by value.
- **Price comparison:** one Product across Quotations in USD per UOM, with a warning when the Incoterms differ.
- **Source status** and a **CSV download**.

## CSV export

```sh
uv run sdi export lines.csv
```

Writes one row per line with current values (latest correction, else extracted), calculated prices, USD per UOM with its FX rate, review status (`needs_review`, `auto_ok` or `reviewed`), corrected fields, Review reasons, and the Source path + line number. Needs no API key. The UI's download is the same file.

`output/quotation_lines.csv` was made this way, from a fresh `sdi.db`:

```sh
SDI_DB_PATH=/tmp/fresh.db SDI_SECOND_READ_MODEL=gemini-3.5-flash-lite uv run sdi ingest data
SDI_DB_PATH=/tmp/fresh.db uv run sdi export output/quotation_lines.csv
```

## Eval

```sh
uv run sdi eval data samples                # may call the model for uncached requests
uv run sdi eval data samples --cached-only  # never calls the model
```

Scores the pipeline against the hand-labelled gold set in `eval/gold/`, ingesting into a fresh temporary database each run. `samples/` holds the synthetic Excel price list added as the extensibility proof. See [`eval/README.md`](eval/README.md) for the metrics and for the model setting the committed results used.

## Development

```sh
uv run pytest            # deterministic parts; no model calls
uv run ruff check .
uv run mypy
```

`scripts/worktree-setup.sh` copies `.env`, the LLM cache and the virtualenv into a new git worktree, for running agents in parallel.
