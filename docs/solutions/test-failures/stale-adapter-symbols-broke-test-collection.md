---
title: Stale adapter symbol imports in test_unit.py broke test collection
module: grc_agent
component: adapter.py / test_unit.py
problem_type: test_failure
tags: [test-collection, import-error, symbol-rename, placement-algorithm, stale-import]
severity: high
date: 2026-07-13
---

## Problem

`tests/test_unit.py` failed to collect at all (ImportError), blocking the
entire test suite, because three symbols had been removed or renamed in
`adapter.py` while the test file was not updated:

1. `BLOCK_COLUMN_MAX_ROWS` — a module-level constant used by the old
   column-layout block-placement algorithm, removed when the algorithm was
   replaced with a spiral AABB-checked grid search.

2. `_footprints_overlap` — renamed to `_rects_overlap` with a different
   call signature: old signature was `_footprints_overlap(a: tuple, b: tuple)`,
   new signature is `_rects_overlap(ax, ay, bx, by)` (four scalars, not two
   tuples), to match the AABB convention.

The test `test_change_graph_add_blocks_batch_wraps_column` also encoded the
old column-layout's specific structural guarantee ("first N blocks share the
same x-coordinate") which no longer applies to the spiral placement.

## Symptoms

```
ImportError: cannot import name 'BLOCK_COLUMN_MAX_ROWS' from 'grc_agent.adapter'
ERROR collecting tests/test_unit.py
Interrupted: 1 error during collection
```

No tests in `test_unit.py` ran at all.

## Root Cause

The adapter's block-placement algorithm was rewritten (column layout → spiral
AABB grid search), and its associated constants/helpers were renamed/removed.
The test file was not updated to match.

## Fix

1. Remove `BLOCK_COLUMN_MAX_ROWS` from the import list.
2. Replace `_footprints_overlap` with `_rects_overlap` in the import.
3. Update all 4 call sites: `_footprints_overlap(a, b)` → `_rects_overlap(*a, *b)`.
4. Rewrite `test_change_graph_add_blocks_batch_wraps_column` to assert the
   spiral placement's actual guarantee: no overlaps for a batch of 12 blocks
   (using `_rects_overlap`), instead of the column-layout column-boundary check.

## Prevention

When renaming or removing a symbol from `adapter.py`, grep `tests/` for all
usages of that symbol before committing. The test suite for placement logic is
in `test_unit.py` and directly imports private helpers (`_rects_overlap`,
`_find_block_placement`) — these are intentional (testing private helpers
directly is acceptable for the core placement algorithm) but means refactors
need to keep the test in sync.
