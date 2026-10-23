# Agent session summary

*Written by the agent (Claude Code, Claude Opus 5.5) for issue #13, from the Claude Code transcripts of every session on this repo: about 20 sessions on 23 October 2026, from 17:56 to 00:26 CEST. The author's turns are quoted from the transcripts. Typos are kept; nothing else is edited.*

## How the work was organised

The author set up the process, and agents did the typing inside it.

1. **Research before any agent.** The author wrote the approach sketch before the first session. It is committed unchanged as `a3d0fba`, so the history shows the starting point.
2. **Planning by interrogation** (`/grill-with-docs`, about 70 minutes). The agent read the brief, the sketch and every file in `data/`, then asked 23 numbered design questions over four rounds. The author answered each one, pushed back on several, and asked what unfamiliar terms meant. The outcome was `GLOSSARY.md`, five ADRs, a rewritten approach doc and a `CLAUDE.md` with the commit and generalisation rules. All of it was committed before any code.
3. **Spec, then tickets.** `/to-spec` produced issue #1 (84 user stories). `/to-tickets` split it into 12 vertical slices (#2–#13) with blocking links. The first slice was a tracer bullet: one JSON Source all the way to SQLite.
4. **Parallel implementation.** The author ran agents in separate git worktrees through Orca, with up to three tickets in flight at once. Each one started with `/implement <issue>`: test-first work, then two review sub-agents (standards and spec) on the diff, then a report of open decisions back to the author. The author decided those, asked for `/pr`, and merged. That gave 13 merged PRs (#14–#27), 99 non-merge commits and 138 tests.

## Where the author steered

These are the points where the author's call changed the design. The agent's original proposal comes first, then what the author said, then what was built.

**Planning**

- **Review rule for images (Q6).** The agent proposed that every image-derived line needs review. The author: *"I would not make all image-derived lines need a review. That means in practice all images would need a human review and hence what is the point. What alternative criterias can we have here to require a human review?"* This produced the per-field verified/unverified/failed model and ADR-0004. A line is reviewed only when a critical field fails or can't be verified.
- **Review output (Q9).** The sketch had an HTML report. The author: *"why don't we implement a simple UI to read the sqlite data and let the user interact with it? Also, we could add the functionality to export as CSV."* On choosing Streamlit: *"note that in production we would make a real fastapi + frontend stack."*
- **Alternative offers (Q5).** The agent planned an `is_alternative` line type for options mentioned in notes. The author: *"why don't we just skip that and write down that we do not support alternative offers for now? If this has only been seen in one document maybe it's better to put it aside rather than implement a new special field with custom logic for it."* It became a stated limitation.
- **Dosage form (Q13).** The agent proposed an extra coarse "form group" for Price comparison. The author pointed out that the sketch already had `dosage_form`. The agent then found a hole in its own idea: a form group would equate prolonged-release and ordinary tablets. The result was an exact standardized dosage form.
- **FX (Q14).** The sketch and the agent's proposal both used a hard-coded, dated rates table. The author: *"can't we fetch them from a public API or a library for free? Do we need a config file for exchange rates?"* The result was Frankfurter at the issue-date ECB rate, cached in SQLite.
- **Model provider (Q10, Q22).** The author first wanted to run the pipeline on their Claude subscription. The agent checked Anthropic's docs and terms and found that subscription tokens are meant for Claude Code, not custom pipelines. The author asked about cost (*"I am not so comfortable paying for this myself"*) and picked the Gemini free tier. They also limited the scope: *"no need to make it model agnostic for a POC. In production we would probably make it model agnostic with fallbacks."* (ADR-0005.)
- **Eval metrics (Q17).** The agent proposed accuracy and flagging quality. The author added review load: *"we would need to track the percentage of fields flagged no? We need to know how many manual reviews we would need."* It is metric 4 in `eval/results.md`.
- **Domain terms.** The author asked what UOM, INN and Incoterm mean. The agent's answers went into `GLOSSARY.md`, which every later session used.

**Implementation**

- **Conflicts (#7).** The author settled the open points: *"require a suplier name"*. They asked for recommendations on unmatched lines, Quotation-level terms and placeholder references, then approved: *"yes, do all three and file the ticket."* That became `NO_SUPPLIER`, `UNMATCHED_IN_QUOTATION`, the no-digit placeholder rule, and issue #18 (term Conflicts, PR #27).
- **Review decisions across re-ingest (#9).** This was the biggest design change during implementation. The agent had kept decisions across re-runs by matching on line number. The author asked: *"where have we set this requirement that reingesting keeps reviews? If we reingest, it is possible that the line disappears … Wouldn't it make it simpler to restart from scratch when reingesting? Also, how often would we expect to have reingestions in reality?"* The agent traced the requirement to its own user story, not the brief, and agreed it could put a correction on the wrong product. The result was that unchanged Sources are skipped by fingerprint, and a re-read Source starts a fresh review. The author then checked the agent's follow-through: *"have you update the spec issue and the subissues about this decision too?"* It hadn't; it then updated #1, #10, #11 and #13. The author also proposed the `reviewed` status (*"why not adding a new status called reviewed?"*), which replaced a separate `approved` column.
- **Image span check (#11).** The author asked: *"what is the thousands-separator issue?"* The agent explained: on the glare scan the model copied `120,000` as evidence but returned 120, and the arithmetic check passed because the line total was misread by the same factor. The author: *"implement the span check now."* Wrong image fields flagged went from 6 to 11 on that run.
- **Second-read fallback and pairing (#6).** The author asked for recommendations on four pending decisions and accepted them. A failed second read now keeps the first read. Reads aren't paired when their products differ by position. Mixed text/scan PDFs and the `scan_02` 3-of-6 result are documented rather than tuned.
- **Quota and deadline.** The Gemini second-read quota ran out before the eval was complete. The agent offered to wait for the daily reset. The author: *"let's not wait and get the results today."* The eval re-ran with the extraction model doing the second read too, and `eval/README.md` states the caveat.
- **Gold set.** The agent drafted the gold set. The author validated it before the final eval run.
- **Tooling.** The author asked for an Orca worktree setup script, checked whether `/implement-spec` would redo closed tickets before using it, and had the agent fix an old-database crash inside the UI PR rather than leave it.

## What the agent contributed, and where it was wrong

**Contributed.** The agent inspected the data pack before planning. It found that both images are scans of the Andina PDF, that the email corrects a price in a P.S., that prices are quoted per tablet, per box and per ampoule, and which products overlap across suppliers. It also drafted the ADRs, spec, tickets, code, tests, gold set and docs. Its review sub-agents caught real defects before merge, including:

- an image line with nothing verified that still came out `auto_ok`;
- databases from an earlier schema that failed every insert;
- a failed second read that discarded a good first read;
- µg read as g.

**Wrong or corrected.**

- Three proposals were overruled or withdrawn after the author's questions: review for every image line, the form-group layer, and carrying decisions across re-ingest.
- A script bug gave every new ticket the wrong description. The agent caught and fixed it before linking the tickets.
- Several reviews found docs committed after the code that changed them, against the `CLAUDE.md` rule. One commit message (`e1cf862`) overstates its claim. Since history isn't rewritten, these were recorded rather than fixed.
- In this session, a failed `git clone` left a shell in the worktree, and one setup line was appended to the author's `.env`. The agent noticed, removed the line, and redid the check.
