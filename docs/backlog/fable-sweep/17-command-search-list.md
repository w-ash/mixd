# 17 — CommandSearchList: de-fork the track-search dialogs

> Spoke of [The Fable Sweep](README.md) ([v0.8.12](../v0.8.12.md)). Self-contained work order — written so a fresh agent can execute it cold, without re-reading the whole codebase.

**Area:** web · **Suggested executor:** Opus · **Effort:** M · **ROI:** med · **Risk:** med · **Status:** Done (v0.8.15)

## Problem

The `cmdk` search-list scaffold is duplicated 3×. `components/shared/TrackSearchCombobox.tsx:39-95` and `components/playlist/AddTracksDialog.tsx:129-204` render a **verbatim** Command shell + 3-state list ladder + identical track-result row:

```tsx
{deferredSearch.length < 2 && (<Command.Empty>Type at least 2 characters to search.</Command.Empty>)}
{deferredSearch.length >= 2 && isLoading && (<Command.Loading>Searching…</Command.Loading>)}
{deferredSearch.length >= 2 && !isLoading && results.length === 0 && (<Command.Empty>No tracks found.</Command.Empty>)}
```

Both call `useTrackSearch()` + `useListTracksApiV1TracksGet({enabled: deferredSearch.length >= 2})` and render `title + formatArtists(track.artists) + ConnectorIcon` rows. `AddTracksDialog` (v0.8.11) forked the combobox to add multi-select instead of composing it. `components/shared/TagAutocomplete.tsx:63-116` shares the same Command shell in a different domain.

## Why it matters

Maintainer: three copies of search-UX logic that must stay in sync (debounce threshold, a11y, empty copy) — the v0.8.11 fork proves the drift mechanism. User: indirect — consistent search behavior in every picker (flows 3.4, 2.1).

## Proposed change

1. `components/shared/CommandSearchList.tsx`: the shell — input binding, `deferredSearch` threshold, the 3-state ladder, and a `renderItem(item)` slot; selection mode (`single | multi`) as a prop or via composition.
2. `components/shared/TrackResultRow.tsx`: the `title + formatArtists + ConnectorIcon` row both track pickers use.
3. Rebuild `TrackSearchCombobox` (single-select) and `AddTracksDialog`'s list (multi-select) on the shell; migrate `TagAutocomplete` to the shell if it fits without contortion (different item shape — judge on the diff; skipping it is acceptable, note why).

## Blast radius & behavior-preservation

Three components + their consumers (`AddTracksDialog` used from PlaylistDetail; combobox used in link/relink flows). Keyboard navigation, focus management, debounce timing, min-2-chars threshold, and copy must be identical — cmdk a11y behavior is the risk surface.

## Test plan

Existing: `pnpm --prefix web test src/components/playlist/AddTracksDialog.test.tsx` + combobox/tag tests — these pin selection behavior and empty/loading states. Add: focused `CommandSearchList` suite (threshold, loading, empty, keyboard selection).

## Guardrails (do not skip)

- **Clean break:** forked scaffold deleted from both dialogs.
- **Grep gate:** `git grep -c 'Type at least 2 characters' web/src` returns exactly 1 (the shell).
- **Green:** `pnpm --prefix web test` + `pnpm --prefix web check` stay green.
- **Scope discipline:** don't change search API calls or add fuzzy matching; `components/ui/**` untouched.

## Notes / counter-proposal

Sequence after spoke 16 if both approved (shared-components directory conventions).
