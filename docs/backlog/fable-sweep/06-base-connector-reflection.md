# 06 — BaseAPIConnector: replace get_playlist reflection with an explicit contract

> Spoke of [The Fable Sweep](README.md) ([v0.8.12](../v0.8.12.md)). Self-contained work order — written so a fresh agent can execute it cold, without re-reading the whole codebase.

**Area:** infrastructure · **Suggested executor:** Opus · **Effort:** M · **ROI:** med · **Risk:** med · **Status:** Completed (2026-07-02, v0.8.16, Opus in worktree; Fable-reviewed and verified on main)

## Problem

`BaseAPIConnector.get_playlist` (`src/infrastructure/connectors/base.py:200-249`) locates the service implementation by **string-built reflection**: `method_name = f"get_{self.connector_name}_playlist"; if hasattr(self, method_name): ...` followed by `on_page` forwarding guarded by **TypeError-message sniffing** (`except TypeError as e: if "on_page" not in str(e): raise`). The inline comment admits the try/except exists "so mock-based tests that assert the plain signature … keep working." This is triple-fragile: renaming a connector breaks dispatch silently at runtime; a genuine TypeError inside the implementation containing "on_page" is swallowed into a retry-without-kwarg; and type checkers see none of it (a `cast` to `Callable[..., Awaitable[ConnectorPlaylist]]` papers over it).

Also in `base.py`: `get_connector_config` (169-198) maintains a `key_mapping` dict translating legacy UPPER_CASE keys ("BATCH_SIZE" → "batch_size") — check callers; if all pass modern lowercase names (or can be updated in one pass), the mapping layer is dead weight.

## Why it matters

Maintainer: this is the shared base every connector inherits; its dispatch idiom is what a new connector author copies. Reflection dispatch here is the single worst pattern in an otherwise well-typed connector layer. User: none — behavior identical.

## Proposed change

1. Replace the reflection with a normal abstract/optional method: `async def _get_playlist_impl(self, playlist_id: str, *, on_page: Callable[[int, int], Awaitable[None]] | None = None) -> ConnectorPlaylist: raise NotImplementedError(f"Playlist operations not supported by {self.connector_name}")`. Each playlist-capable connector overrides it (Spotify: rename `get_spotify_playlist` → `_get_playlist_impl`, accepting `on_page`; same for Last.fm if it has one — `git grep 'def get_.*_playlist' src/infrastructure/connectors/` to enumerate). `get_playlist` becomes a 3-line delegation.
2. Kill the TypeError sniffing: `on_page` is part of the signature; implementations that don't paginate simply ignore it (`_ = on_page`).
3. Update the mock-based tests that asserted the old per-service method names to target `_get_playlist_impl`.
4. `get_connector_config`: enumerate callers (`git grep 'get_connector_config('`); if all keys can be modern lowercase, delete `key_mapping` and pass through directly.

## Blast radius & behavior-preservation

Every connector subclass + tests that mock `get_spotify_playlist`-style names. Runtime behavior identical: same method resolution result, same NotImplementedError for non-playlist connectors, same on_page forwarding for Spotify. The removed path (TypeError retry) only fired for implementations lacking `on_page` — after this change the signature guarantees it exists.

## Test plan

Existing: `uv run pytest tests/ -k "connector and playlist"` + full fast suite (mocks will surface every missed rename). No new tests; the contract is now checked statically.

## Guardrails (do not skip)

- **Clean break:** no `get_spotify_playlist` alias left behind; every call site + mock updated.
- **Grep gate:** `git grep 'get_spotify_playlist\|hasattr(self, method_name)\|"on_page" not in str'` returns nothing when done.
- **Layer flow:** unchanged.
- **Green:** `uv run pytest` stays green; tests re-targeted, not weakened.
- **Ratchet:** removes two `cast()`s; check basedpyright warning delta.
- **Scope discipline:** `BaseMetricResolver` and `register_metrics` in the same file are healthy — untouched.

## Notes / counter-proposal

None.
