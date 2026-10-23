# Eval

Measures the real extractor (Gemini, via the LLM cache) against a hand-labelled gold set for the sample Sources.

```sh
export SDI_SECOND_READ_MODEL=gemini-3.5-flash-lite   # as the committed results were run; see below
uv run sdi eval data samples                 # may call the model for uncached requests
uv run sdi eval data samples --cached-only   # never calls the model; reproducible and free
```

Each run ingests into a fresh temporary database, so the numbers always reflect the current code. It writes `eval/results.md` and `eval/results.json`, and prints how many model requests the LLM cache couldn't answer. A run with `0` there made no new calls. FX rates are not cached between runs, because the database is fresh: each run asks Frankfurter for the same historical ECB rates. If it can't be reached, lines get an `FX_UNAVAILABLE` flag and the review-load numbers move.

## Gold set

`gold/<source file name>.json`, one per Source, with the Quotation terms and the expected Quotation lines.

- A field left out isn't scored. For example, `quantity` is left out where a Source gives only an MOQ.
- `null` means the field should come back empty: hidden scan values, or a price the document doesn't state.
- `{"any_of": [...]}` accepts any listed value: partly visible scan values, or equivalent dosage-form terms.
- `notes` explains each judgement call.

**Status: drafted by the agent, checked by the author.**

## Metrics

Reported per Source type (the gold file's `source_type`) and overall.

1. **Line matching:** expected lines found, missed, and extra extracted lines. Lines are paired by shared INNs, then Brand name, then position.
2. **Field accuracy** on matched lines, plus Quotation terms once per Source. Numbers count as correct within 0.5%; strings are compared ignoring case, punctuation and spacing.
3. **Flagging quality:** the share of wrong fields that were flagged. A field counts as flagged when verification failed it or a Review reason names it.
4. **Review load:** the share of lines needing review, the share of checked fields that are flagged, and flags on correct non-empty values (false alarms).

## Caveat on the committed results

The second reads in the committed results come from the extraction model (`gemini-3.5-flash-lite`), not the stronger default second-read model. That model's free-tier daily quota (20 requests) ran out while the cache was being filled and hadn't reset when the results were due. The second reader draws its own seeds, so it samples a fresh read instead of getting the first read back from the cache. But two reads from the same model share more of their errors than reads from a lighter and a stronger model, so second-read agreement here is weaker evidence than the design intends.

Two runs with `--cached-only` gave byte-identical results with 0 cache misses. To reproduce them, keep the `SDI_SECOND_READ_MODEL` override above. Without it, the second reads aren't cached and a `--cached-only` run reports them unavailable.
