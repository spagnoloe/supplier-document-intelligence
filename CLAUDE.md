# CLAUDE.md

Take-home assignment. Design lives in `docs/approach.md` and `docs/adr/`; read both before changing architecture.

## Commits

Reviewers read the git history as closely as the code, so commit at every meaningful step: one commit per decision or working increment.

- Message subject and body explain **why**: what broke, what was rejected, what changed your mind. Example: "Derive unit price in code; LLM-computed prices drifted on per-box quotes".
- When a decision changes, commit the revised doc or ADR alongside the code that caused it.
- History stays as committed: new commits on top, never squash or rewrite.

## Generalisation

The pack in `data/` is a sample, not the spec. Supplier names, filenames, field names and layouts from `data/` stay out of code and prompts; a new supplier or layout must work with zero code changes.

## Agent skills

### Issue tracker

Issues live in GitHub Issues on spagnoloe/supplier-document-intelligence (via `gh`). See `docs/agents/issue-tracker.md`.

### Triage labels

Default vocabulary: needs-triage, needs-info, ready-for-agent, ready-for-human, wontfix. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `GLOSSARY.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.
