#!/usr/bin/env bash
set -euo pipefail

out=".superpowers/sdd/phase2-task-1-review-package.diff"
files=(
  "tests/test_phase2_mock_documents.py"
  "data/documents/attractions/chengdu.md"
  "data/documents/weather/chengdu.md"
  "data/documents/transport/chengdu.md"
  "data/documents/accommodation/chengdu.md"
  "data/documents/food/chengdu.md"
)

{
  printf '# Phase 2 Task 1 working-tree review package\n\n'
  printf '## Tracked diff\n'
  git diff -U10 -- app/rag/document_loader.py
  printf '\n## New files\n'
  for file in "${files[@]}"; do
    git diff --no-index -U10 /dev/null "$file" || true
  done
} > "$out"
