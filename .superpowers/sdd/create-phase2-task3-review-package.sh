#!/usr/bin/env bash
set -euo pipefail

out=".superpowers/sdd/phase2-task-3-review-package.diff"
{
  printf '# Phase 2 Task 3 working-tree review package\n\n'
  printf '## Tracked diff\n'
  git diff -U10 -- app/schemas/planning.py
  printf '\n## New implementation\n'
  git diff --no-index -U10 /dev/null app/agents/workers/rag_analysis.py || true
  printf '\n## Test diff\n'
  git diff --no-index -U10 /dev/null tests/test_phase2_rag_workers.py || true
} > "$out"
