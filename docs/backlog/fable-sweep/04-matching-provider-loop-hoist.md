# 04 — Matching providers: hoist the per-track loop, drop unreachable validation

> Spoke of [The Fable Sweep](README.md) ([v0.8.12](../v0.8.12.md)). Self-contained work order — written so a fresh agent can execute it cold, without re-reading the whole codebase.

**Area:** infrastructure · **Suggested executor:** Opus · **Effort:** S · **ROI:** med · **Risk:** low · **Status:** Not Started

## Problem

`BaseMatchingProvider.fetch_raw_matches_for_tracks` (`_shared/matching_provider.py:80-196`) already partitions tracks via `_has_isrc` / `_has_artist_and_title` before calling the subclass hooks. Yet both template subclasses re-validate inside their hooks and duplicate an identical per-track loop shell:

- `spotify/matching_provider.py:70-101` and `:143-177` — `for track in tracks: if not track.id: continue; if not track.isrc: append NO_ISRC failure; try: ... except: handle_track_processing_failure(...)`.
- `musicbrainz/matching_provider.py:66-97` and `:168-194` — same shell, same failure messages ("Track missing ISRC code", "Track missing artist or title data").

The `if not track.isrc` / `if not track.artists or not track.title` branches are **unreachable via the template** (the partition guarantees the invariant), so they are defensive dead code that inflates both files. The loop shell (validate → try → classify failure) is ~80 duplicated lines across the two providers.

## Why it matters

Maintainer: the next provider (Apple Music, eventually) copies one of these files; the copy should be ~half the size. User: none — matching results identical.

## Proposed change

1. Add a protected helper to `BaseMatchingProvider`:
   `async def _match_each(self, tracks, method: str, matcher: Callable[[Track], Awaitable[tuple[RawProviderMatch | None, MatchFailure | None]]]) -> tuple[dict[UUID, RawProviderMatch], list[MatchFailure]]` — owns the loop, the `track.id` guard, and the exception → `handle_track_processing_failure` classification.
2. Spotify `_match_by_isrc` / `_match_by_artist_title` become 3-5 line calls wiring `_match_track_by_isrc` / `_match_track_by_artist_title_one` through `_match_each`. Same for MusicBrainz's per-track artist/title path (its ISRC path is batch — leave `_lookup_isrc_batch` as is).
3. Delete the unreachable NO_ISRC / NO_METADATA re-validation branches in both subclasses (the base partition is the single validation point — note this in the base docstring).

`LastFMProvider` deliberately does not inherit the base (batch API, documented LSP rationale at `lastfm/matching_provider.py:6-11`) — leave it untouched.

## Blast radius & behavior-preservation

Callers construct providers via config factories (`create_matching_config` / registry). Match output must be identical; the only removed branches are unreachable through the template. Direct-call tests that invoke `_match_by_isrc` with invalid tracks (bypassing the template) may exist — if a test exercises the unreachable branch, update the test to go through `fetch_raw_matches_for_tracks` (that is re-truing the test to the real contract, not weakening it).

## Test plan

Existing: `uv run pytest tests/ -k "matching_provider or match_provider"`. No new tests required if the suite covers ISRC-hit, artist-title-hit, API-failure, and no-result paths per provider; add the missing case otherwise.

## Guardrails (do not skip)

- **Clean break:** no shims; both subclasses migrate to `_match_each` in this spoke.
- **Grep gate:** `git grep 'Track missing ISRC code' src/infrastructure/connectors/spotify src/infrastructure/connectors/musicbrainz` returns nothing (the message lives only in the base partition failure).
- **Layer flow:** unchanged; business logic (confidence, thresholds) stays in domain.
- **Green:** `uv run pytest` stays green; no test weakened to pass.
- **Ratchet:** no rule flip expected.
- **Scope discipline:** don't touch `LastFMProvider` or the domain evaluation service.

## Notes / counter-proposal

Audit disposition: the matching-provider family is otherwise **healthy** — base template + thin subclasses + a documented LSP-justified exception. The seed's "matching_provider ×4" duplication is this loop shell only.
