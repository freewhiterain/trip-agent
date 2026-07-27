#!/usr/bin/env bash
set -euo pipefail

out=".superpowers/sdd/phase2-task-2-review-package.diff"
{
  printf '# Phase 2 Task 2 working-tree review package\n\n'
  printf '## Tracked diff\n'
  git diff -U10 -- app/agents/workers/local_knowledge.py
  printf '\n## New test file\n'
  git diff --no-index -U10 /dev/null tests/test_phase2_rag_workers.py || true
} > "$out"
