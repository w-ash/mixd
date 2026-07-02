# 03 — Play-resolver decomposition & Last.fm chain flattening

> Spoke of [The Fable Sweep](README.md) ([v0.8.12](../v0.8.12.md)). Self-contained work order — written so a fresh agent can execute it cold, without re-reading the whole codebase.

**Area:** infrastructure · **Suggested executor:** Opus · **Effort:** M · **ROI:** med · **Risk:** med · **Status:** Not Started

## Problem

1. **`SpotifyConnectorPlayResolver.resolve_connector_plays` is a ~195-line method** (`src/infrastructure/connectors/spotify/play_resolver.py:84-278`) doing five jobs inline: ID/hint extraction, canonical resolution, three-way filtering (resolution-failure / duration / incognito), a ~60-line context-dict literal (lines 185-239), and metrics assembly. It is a suppressed-PLR hotspot by shape.
2. **The Last.fm resolution chain has a thin middle layer.** `LastfmConnectorPlayResolver` (199 lines) → `LastfmTrackResolutionService` (146 lines, `lastfm/track_resolution_service.py`) → `LastfmInwardResolver`. The middle service only extracts unique `artist::title` identifiers, delegates, and maps results back to input order (its own docstring: "Thin orchestration layer"). Its progress_callback percentages (10/30/80/100) are cosmetic.
3. **Both resolvers duplicate a skeleton**: resolve → iterate plays → failure-info dict (`{"track": f"{artist} - {name}", "reason": "track_resolution_failed"}` — spotify:141-146 ≡ lastfm:77-81) → context build → `TrackPlay(...)` construction → metrics merge → identical `_create_empty_metrics` shape.

## Why it matters

Maintainer: play resolution is where imported history becomes queryable data (flows 4.2/4.3, the Curator's history archaeology). A 195-line method is where the next duration-filter bug hides. User: indirect.

## Proposed change

1. **Split `resolve_connector_plays` (spotify)** into private helpers along its own step comments: `_extract_ids_and_hints(plays)`, `_should_skip(play, track) -> SkipReason | None` (duration + incognito), `_build_context(play, spotify_id) -> dict`, `_assemble_metrics(...)`. Public behavior identical.
2. **Fold `LastfmTrackResolutionService` into `LastfmConnectorPlayResolver`** (or directly into `LastfmInwardResolver` usage): the identifier-extraction + order-mapping become private methods of the resolver. Delete `lastfm/track_resolution_service.py`. Check its only other consumers first (`git grep LastfmTrackResolutionService`) — if the CLI or an orchestrator constructs it directly, update those call sites to the resolver.
3. **Hoist the tiny shared bits** (failure-info dict shape, empty-metrics factory) into `_shared` only if it stays under ~30 lines — otherwise leave duplicated; two sites is below the DRY threshold.

## Blast radius & behavior-preservation

Consumers resolve via `play_import_registry` / `play_import_orchestrator.py`. `resolve_connector_plays(connector_plays, uow, *, user_id, progress_callback)` signature unchanged. Filtering rules (`should_include_spotify_play`), context keys (incl. `architecture_version`), and metrics keys must be byte-identical — the context dict is persisted into `track_plays.context` JSON, so any key change is user-visible data drift.

## Test plan

Existing: `uv run pytest tests/ -k "play_resolver or resolve_connector"`. Add a characterization test first if none pins the context-dict keys for a resolved Spotify play (assert exact key set) — that is the riskiest surface. Reuse `make_mock_uow`.

## Guardrails (do not skip)

- **Clean break:** no shims/aliases/re-export layers; `LastfmTrackResolutionService` deleted with all call sites updated.
- **Grep gate:** `git grep 'LastfmTrackResolutionService'` returns nothing when done.
- **Layer flow:** inward-only; unchanged.
- **Green:** `uv run pytest` stays green; no test weakened to pass.
- **Ratchet:** verify the spotify method's PLR counts clear (`uv run ruff check src/infrastructure/connectors/spotify/play_resolver.py --select PLR0914,PLR0915,PLR0912`).
- **Scope discipline:** the `architecture_version: "connector_plays_deferred_resolution"` context key is archaeology baked into persisted data — logged in hub Deferred; keep writing it here.

## Notes / counter-proposal

Pairs with spoke 02 (same subsystem). If both run, do 02 first — it may reshape the orchestrator boundary this spoke touches.
