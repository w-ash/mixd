# 16 — QueryStates wrapper + skeleton primitives: kill the four-state ladder

> Spoke of [The Fable Sweep](README.md) ([v0.8.12](../v0.8.12.md)). Self-contained work order — written so a fresh agent can execute it cold, without re-reading the whole codebase.

**Area:** web · **Suggested executor:** Fable · **Effort:** L · **ROI:** high · **Risk:** med · **Status:** Done (v0.8.15)

## Problem

Every list/detail screen hand-rolls the same `isLoading → isError → empty → success` branch ladder inline plus a bespoke local `*Skeleton` component. No shared wrapper exists, so the design system's "four states for every data view" is re-derived per page. Quoted shape (three of ~10 sites):

- `pages/settings/Integrations.tsx:203` — `{isLoading && <IntegrationsSkeleton />} {isError && (<QueryErrorState …/>)} {!isLoading && !isError && connectors.length === 0 && (<EmptyState …/>)} {!isLoading && !isError && connectors.length > 0 && (…)}`
- `pages/settings/Tags.tsx:273` — same ladder with `isPending`.
- `pages/settings/ImportHistoryPage.tsx:159` — same ladder.

Screens sharing it: `Dashboard`, `Playlists`, `Workflows`, `Library`, settings `Integrations`/`Tags`/`ImportHistoryPage`/`Sync` (+ `Account` as a lighter variant). **14 local `*Skeleton` functions** duplicate the shimmer-row markup: `Workflows.tsx:30`, `Playlists.tsx:37`, `WorkflowDetail.tsx:28`, `Dashboard.tsx:51,236`, `WorkflowRunDetail.tsx:55`, `TrackDetail.tsx:42`, `settings/Tags.tsx:41`, `settings/Account.tsx:13`, `settings/Integrations.tsx:65`, `TemplateGalleryDialog.tsx:69`, `ConnectorPlaylistPickerDialog.tsx:86`.

## Why it matters

Maintainer: highest-leverage frontend cleanup — ~10 screens × ~15 lines of ladder + 14 skeletons. Every new page copies the ladder; inconsistencies already crept in (`isLoading` vs `isPending`, `EmptyState role="alert"` vs `QueryErrorState`). User: indirect but real — consistent loading/error/empty behavior on every screen (flows 2.1, 3.1, 6.1, 7.1).

## Proposed change

1. `components/shared/QueryStates.tsx`: a wrapper taking `{query, skeleton, empty, errorHeading, children(data)}` (or render props) that renders the four states in the canonical order. Follow the design system (frontend-design skill) for spacing/tokens.
2. 2–3 skeleton primitives: `<ListRowsSkeleton rows={n}/>`, `<CardGridSkeleton/>`, `<TableSkeleton/>` in `components/shared/skeletons.tsx` — parameterized enough to replace the 14 locals (audit each before deleting; a couple have bespoke shapes worth keeping as thin compositions of the primitives).
3. Migrate all listed screens; delete the local skeletons and ladders.
4. Normalize the error state: every screen uses the same error component + `role="alert"` semantics.

## Blast radius & behavior-preservation

Pure presentational refactor — same states, same copy, same test hooks. Component tests that query skeleton test-ids or empty-state copy must keep passing; where a test asserted a local skeleton's structure, re-point it at the primitive. Visual parity: shimmer shapes may normalize slightly across screens — acceptable within "behavior-preserving" as long as states and copy are identical (call out any visible normalization in the PR).

## Test plan

Existing: `pnpm --prefix web test` — the page suites assert loading/error/empty rendering (e.g. `Library.test.tsx`, `Tags` tests). Add: one focused suite for `QueryStates` covering the four states + the `isPending`/`isLoading` prop variants.

## Guardrails (do not skip)

- **Clean break:** local `*Skeleton` components deleted; no page keeps a private ladder.
- **Grep gate:** `git grep -n '!isLoading && !isError' web/src/pages` returns nothing when done.
- **Layer flow:** n/a (frontend); respect `components/ui/**` (shadcn) untouched.
- **Green:** `pnpm --prefix web test` + `pnpm --prefix web check` stay green.
- **Ratchet:** n/a.
- **Scope discipline:** do NOT redesign empty-state copy or add features; `api/generated/**` untouched.

## Notes / counter-proposal

Biggest frontend spoke; suggest running it before 17/19 (both build on the shared components it creates).
