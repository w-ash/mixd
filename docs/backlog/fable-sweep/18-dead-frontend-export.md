# 18 — Resolve dead export hasActiveFilters

> Spoke of [The Fable Sweep](README.md) ([v0.8.12](../v0.8.12.md)). Self-contained work order — written so a fresh agent can execute it cold, without re-reading the whole codebase.

**Area:** web · **Suggested executor:** Haiku · **Effort:** XS · **ROI:** low · **Risk:** low · **Status:** Not Started

## Problem

`web/src/lib/filters-to-workflow.ts:68` exports `hasActiveFilters(state: LibraryFilterState): boolean` — referenced **only** by its own test (`filters-to-workflow.test.ts`); zero production imports (grep-verified 2026-07-01). `Library.tsx` imports `parsePreferenceParam`/`summarizeFilters`/`filtersToWorkflowDef` from the module but never this function, implying Library re-derives active-filter detection inline or the function was orphaned by a refactor.

## Why it matters

Maintainer: a test-kept-alive export is a lie about the API surface. User: none.

## Proposed change

Check how `Library.tsx` currently decides "filters are active" (e.g. for the save-as-workflow button enablement). Then either:
- **(a)** if Library inlines equivalent logic → replace the inline logic with `hasActiveFilters` (function becomes live), or
- **(b)** if nothing needs it → delete the function + its test cases.

Prefer (a) if the inline logic is semantically identical; otherwise (b).

## Blast radius & behavior-preservation

Option (a): behavior must be provably identical (compare predicates field by field). Option (b): zero runtime impact.

## Test plan

`pnpm --prefix web test src/lib/filters-to-workflow.test.ts src/pages/Library.test.tsx` (adjust names to actual test files).

## Guardrails (do not skip)

- **Grep gate (if deleting):** `git grep 'hasActiveFilters' web/src` returns nothing.
- **Green:** `pnpm --prefix web test` + `pnpm --prefix web check` stay green.
- **Scope discipline:** this spoke is one function; nothing else in `lib/` moves.

## Notes / counter-proposal

Related healthy disposition: `formatClockTime`, `SNAPSHOT_POLL_INTERVAL_MS`, `operationSnapshotQueryKey` are exported only for their own unit tests — acceptable test-seam exports, leave them.
