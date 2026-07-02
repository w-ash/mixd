# 15 — Backend minor batch: dual_mode consistency, play-history predicate, protocol surface

> Spoke of [The Fable Sweep](README.md) ([v0.8.12](../v0.8.12.md)). Self-contained work order — written so a fresh agent can execute it cold, without re-reading the whole codebase. Groups three verified XS findings that share no better home.

**Area:** domain+application · **Suggested executor:** Haiku · **Effort:** S · **ROI:** low-med · **Risk:** low · **Status:** Not Started

## Problem

1. **`dual_mode` helper ignored by 4 factories.** `domain/transforms/core.py:42` defines `dual_mode`, but the idiom `return transform(tracklist) if tracklist is not None else transform` is re-inlined at `application/metadata_transforms/play_history.py:170,286`, `preference.py`, `tag.py`, `shuffle.py:102`. (`combining.py`'s `tracklist or TrackList()` variant is semantically different — leave it.)
2. **Play-history window predicate duplicated.** `filter_by_play_history` (`play_history.py:107-140`) and `sort_by_play_history` (`play_history.py:229-260`) copy the "no window → total; else check `last_played` in window else 0/skip" body against `get_play_metrics`/`parse_datetime_safe`. Extract one predicate/metric-getter into the module's existing `_helpers`.
3. **Two `ConnectorRepository` protocol methods are impl-internal.** `domain/repositories/connector.py:342` `get_remaining_mappings` and `:241` `batch_ensure_primary_mappings` have zero application/domain callers — each is only self-called inside the implementation (`persistence/repositories/track/connector.py:909`, `:773`). Remove from the Protocol; make them private impl methods (`_`-prefixed).
4. **Two playlist-side protocol methods, same class (vulture-flagged, grep-confirmed 2026-07-01):** `domain/repositories/connector.py:437` `get_by_connector_id` (impl `playlist/connector.py:44`) and `domain/repositories/playlist.py:117` `create_links_batch` (impl `playlist/links.py:213`) have zero callers anywhere outside protocol+impl. Delete both protocol entries and impls (or keep as private if the impl self-calls them — check first).
5. **Playlist entity conveniences: production-dead, test-kept-alive.** `domain/entities/playlist.py:112` `PlaylistEntry.is_resolved` — the API converter re-derives `entry.track is not None` inline (`interface/api/schemas/playlists.py:241`) instead of calling it: **wire the property at that call site** (semantic is live; property should be too). `playlist.py:182` `Playlist.resolved_entries` and `:197` `to_tracklist()` — no production callers, only tests (`test_tracklist.py` ×15, `test_playlist_unresolved.py`): verify with `git grep`, then either delete (+ retarget the tests at the surviving behavior) or document why they stay whitelisted. If kept, they stay in `vulture_whitelist.py` with a rationale comment matching the file's existing style.

## Why it matters

Maintainer: consistency debt — each item is small, but together they blur which pattern is canonical. Item 3 shrinks the domain protocol surface that every mock must satisfy. User: none.

## Proposed change

As stated per item. For item 3, update `make_mock_uow`/mock fixtures if they stub these methods.

## Blast radius & behavior-preservation

Items 1–2: pure-function internals, transform outputs identical. Item 3: protocol shrink — compile-time only; impl behavior unchanged.

## Test plan

Existing: `uv run pytest tests/ -k "play_history or transforms or connector_repo"`. No new tests; the transforms suites pin outputs.

## Guardrails (do not skip)

- **Clean break:** old inlined idioms replaced everywhere named above.
- **Grep gate:** `git grep 'if tracklist is not None else transform' src/application` returns nothing; `git grep 'get_remaining_mappings\|batch_ensure_primary_mappings' src/domain` returns nothing.
- **Layer flow:** unchanged; domain protocol shrinks (inward-safe).
- **Green:** `uv run pytest` stays green.
- **Ratchet:** none expected.
- **Scope discipline:** exactly these three items; anything else found goes to the hub.

## Notes / counter-proposal

None.
