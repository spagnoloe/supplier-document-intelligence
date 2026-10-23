#!/usr/bin/env bash
# Prepare a fresh git worktree (e.g. one created by Orca for a parallel agent)
# with the untracked files it needs to run: secrets, the LLM response cache and
# the virtualenv. Run from inside the new worktree. Safe to re-run.
set -euo pipefail

worktree="$(git rev-parse --show-toplevel)"
# The main checkout owns the shared .git dir; its parent is the source of the
# untracked files we copy in.
main="$(cd "$(git rev-parse --path-format=absolute --git-common-dir)/.." && pwd)"

if [[ "$worktree" == "$main" ]]; then
  echo "Already in the main checkout ($main); nothing to copy."
else
  echo "Setting up $worktree from $main"

  # .env: API key and settings. Never overwrite one the worktree already has.
  if [[ -f "$worktree/.env" ]]; then
    echo "  .env exists, leaving it alone"
  elif [[ -f "$main/.env" ]]; then
    cp "$main/.env" "$worktree/.env"
    echo "  copied .env"
  else
    cp "$worktree/.env.example" "$worktree/.env"
    echo "  WARNING: no .env in main checkout; copied .env.example, fill in GOOGLE_API_KEY"
  fi

  # LLM cache: a copy, not a symlink, so parallel agents don't write to one
  # SQLite file. Reusing cached responses keeps agents off the free-tier quota.
  if [[ -f "$main/.cache/llm.db" && ! -f "$worktree/.cache/llm.db" ]]; then
    mkdir -p "$worktree/.cache"
    cp "$main/.cache/llm.db" "$worktree/.cache/llm.db"
    echo "  copied .cache/llm.db"
  fi
fi

# Each worktree gets its own .venv; uv hardlinks from its global cache, so this is fast.
(cd "$worktree" && uv sync)
echo "Worktree ready."
