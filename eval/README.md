# Eval

Measures the real extractor (Gemini, via the LLM cache) against a hand-labelled gold set for the sample Sources.

```sh
uv run sdi eval data samples                 # may call the model for uncached requests
uv run sdi eval data samples --cached-only   # never calls the model; reproducible and free
```

Each run ingests into a fresh temporary database, so the numbers always reflect the current code. It writes `eval/results.md` and `eval/results.json`, and prints how many model requests the LLM cache couldn't answer. A run with `0` there made no new calls.

## Gold set

`gold/<source file name>.json`, one per Source, with the Quotation terms and the expected Quotation lines.

- A field left out isn't scored. For example, `quantity` is left out where a Source gives only an MOQ.
- `null` means the field should come back empty: hidden scan values, or a price the document doesn't state.
- `{"any_of": [...]}` accepts any listed value: partly visible scan values, or equivalent dosage-form terms.
- `notes` explains each judgement call.

**Status: drafted by the agent, not yet checked by the author.** Don't use it to tune anything until the check is recorded.

## Metrics

Reported per Source type (the gold file's `source_type`) and overall.

1. **Line matching:** expected lines found, missed, and extra extracted lines. Lines are paired by shared INNs, then Brand name, then position.
2. **Field accuracy** on matched lines, plus Quotation terms once per Source. Numbers count as correct within 0.5%; strings are compared ignoring case, punctuation and spacing.
3. **Flagging quality:** the share of wrong fields that were flagged. A field counts as flagged when verification failed it or a Review reason names it.
4. **Review load:** the share of lines needing review, the share of checked fields that are flagged, and flags on correct non-empty values (false alarms).

## Caveat on the committed results

The committed results were produced with `--cached-only`. While the cache was being filled, the free-tier daily quota for the second-read model ran out, so image Sources (and the PDF line whose checks failed) have no second read and carry `SECOND_READ_UNAVAILABLE`. That inflates image review load. Once the quota resets, rerun without `--cached-only` to cache the second reads, then rerun with it and commit the results.
