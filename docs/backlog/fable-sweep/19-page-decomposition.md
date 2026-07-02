# 19 — Page decomposition along named seams (Dashboard, TrackDetail, WorkflowRunDetail, Tags)

> Spoke of [The Fable Sweep](README.md) ([v0.8.12](../v0.8.12.md)). Self-contained work order — written so a fresh agent can execute it cold, without re-reading the whole codebase.

**Area:** web · **Suggested executor:** Opus · **Effort:** M-L · **ROI:** med · **Risk:** low-med · **Status:** Not Started

## Problem

Four screens are oversized with clean, already-identified seams (audit 2026-07-01):

1. **`pages/Dashboard.tsx` (565)** — `MatchingHealth` cluster (lines 226-410: skeleton + `groupByCategory` + card/`ResponsiveTable` pair) and `GettingStarted` (`StepCard` + section, 412-510) are self-contained sub-features living inline.
2. **`pages/TrackDetail.tsx` (559)** — `MappingList` (163-330) owns its own mutation + two dialog states — a complete sub-feature; `matchMethods` label/description map (113-158) is static data that belongs in `lib/`.
3. **`pages/WorkflowRunDetail.tsx` (513)** — `PlaylistChangesPanel`/`TrackChangeGroup` (67-138), `NodeExecutionRow` (141-219), `OutputTrackCard`/`OutputTracksTable` (225-336) are three extractable clusters.
4. **`pages/settings/Tags.tsx` (443)** — three near-identical `<ConfirmationDialog>` blocks (rename 370-392, merge 394-416, delete 418-432) differing only in copy/handler — collapse to one data-driven dialog keyed off `activeDialog.mode`.
5. **`pages/PlaylistDetail.tsx` (944)** — main-thread read 2026-07-01: well-organized but five sub-components live inline: `DeletePlaylistDialog` (106-142), `EditPlaylistDialog` (146-285), `LinkPlaylistDialog` (307-519), `LinkedServicesSection` (523-736, ~215 lines — a full sub-feature with two mutations + sync dialog + operation progress), `RepairUnresolvedBar` (742-800). Extract to `components/playlist/`. Also: orphaned doc comment at lines 740-741 (`/** Status mark for an entry... */` above the wrong component — v0.8.8.2 declutter leftover) — delete it.
6. **Judged-on-inspection (extract only if the seam is clean):** `pages/Library.tsx` — the `Library()` body (~530 lines) could shed its filter bar and bulk-actions toolbar; `ConnectorPlaylistPickerDialog.tsx` — the 550-line main body could shed its filter bar + row list (`FilterChip` already extracted). Skipping either is fine; note why.

## Why it matters

Maintainer: these are the screens most edited per feature cycle (dashboard + track detail especially); component extraction makes each sub-feature independently testable. User: none — pixel-identical.

## Proposed change

1. `Dashboard.tsx` → extract `components/dashboard/MatchingHealth.tsx` + `components/dashboard/GettingStarted.tsx`.
2. `TrackDetail.tsx` → `components/track/MappingList.tsx`; `matchMethods` map → `lib/match-methods.ts`.
3. `WorkflowRunDetail.tsx` → `components/workflow/run-detail/` cluster (`OutputTracksTable`, `NodeExecutionRow`, `PlaylistChangesPanel`).
4. `Tags.tsx` → one `<TagActionDialog mode={...}>` replacing the three blocks.
5. Local skeletons in these files are deleted by spoke 16 — if 16 hasn't run, leave skeletons in place and note it.

## Blast radius & behavior-preservation

Move-only refactor: same JSX, same hooks, same test-ids. Props boundaries are the seams the audit named; no state reshaping. Tags dialog collapse must keep exact copy per mode (tests assert dialog text).

## Test plan

Existing: `pnpm --prefix web test src/pages/Dashboard.test.tsx src/pages/TrackDetail.test.tsx src/pages/WorkflowRunDetail.test.tsx` + Tags tests — pass unmodified except import paths. Add none.

## Guardrails (do not skip)

- **Clean break:** inline definitions deleted from pages.
- **Grep gate:** `git grep -n 'function MatchingHealth' web/src/pages` returns nothing when done.
- **Green:** `pnpm --prefix web test` + `pnpm --prefix web check` stay green.
- **Scope discipline:** minor-flagged items NOT here: `NodeConfigPanel` `FieldInput` extraction, `EditorToolbar` `useWorkflowSave` — hub Deferred. `useOperationProgress.ts`/`editor-store.ts` dispositioned healthy-cohesive, leave alone.

## Notes / counter-proposal

Sequence after 16 (skeleton primitives) to avoid double-touching the same files.
