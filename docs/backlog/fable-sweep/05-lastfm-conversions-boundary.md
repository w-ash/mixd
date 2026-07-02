# 05 — Last.fm conversions: validate at the boundary like everyone else

> Spoke of [The Fable Sweep](README.md) ([v0.8.12](../v0.8.12.md)). Self-contained work order — written so a fresh agent can execute it cold, without re-reading the whole codebase.

**Area:** infrastructure · **Suggested executor:** Opus · **Effort:** S · **ROI:** med · **Risk:** low · **Status:** Not Started

## Problem

`convert_lastfm_track_to_connector` (`src/infrastructure/connectors/lastfm/conversions.py:112-200`) is a pre-convention holdover that violates the repo's own boundary rule (".claude/rules/infrastructure-patterns.md": *validate raw `dict[str, Any]` → typed model at the API client; only typed models flow downstream*). It hand-walks a raw `Mapping[str, JsonValue]` with isinstance-dances for artist (dict-or-str, lines 131-140), album (dict-or-str, 143-150), duration (`str(duration_val).isdigit()`, 153-157), and metric fields — exactly what a Pydantic model in `lastfm/models.py` (which already exists, 208 lines, e.g. `LastFMTrackInfoData`) is for. Contrast: `spotify/conversions.py:57-65` and `musicbrainz/conversions.py:25-31` both `model_validate` first.

Minor riders in the same family:
- Function-local imports duplicating module-level ones: `lastfm/conversions.py:123-125` (`datetime`, `Artist`, `ConnectorTrack` — the latter two already imported at line 20), `musicbrainz/conversions.py:91` (`datetime`).
- `spotify/conversions.py:43-45` `validate_non_empty` — check callers (`git grep validate_non_empty`); if unused, delete.

## Why it matters

Maintainer: one connector doing untyped extraction undermines the "only typed models flow downstream" invariant the other two uphold, and this function feeds `ConnectorTrack` rows persisted to the DB (data-quality surface for flow 2.3 bad-mapping correction). User: indirect — fewer silently-wrong conversions.

## Proposed change

1. Add (or reuse) a Pydantic model in `lastfm/models.py` for the raw track shape this function receives (artist dict-or-str and album dict-or-str become validators or union types on the model).
2. `convert_lastfm_track_to_connector(track: LastFMTrackData) -> ConnectorTrack` — typed input; the API client validates before calling. Find callers via `git grep convert_lastfm_track_to_connector` and move validation to those boundaries.
3. Delete the function-local duplicate imports in both files; hoist `datetime` to module level.
4. Delete `validate_non_empty` if grep shows no callers.

## Blast radius & behavior-preservation

Few call sites (likely `lastfm/operations.py` / connector fetch paths). Output `ConnectorTrack` fields must be identical for the same input JSON — including the `connector_track_identifier` fallback chain (mbid → url → `f"lastfm:{title}"`, lines 183-187) and duration seconds→ms conversion. Write the model validators to reproduce these exactly.

## Test plan

Existing: `uv run pytest tests/ -k "lastfm and (conversion or convert)"`. Add a characterization test FIRST with a raw fixture dict (dict-artist, str-artist, missing album, string duration) asserting the exact `ConnectorTrack` output — then refactor against it.

## Guardrails (do not skip)

- **Clean break:** the raw-Mapping signature is gone; every call site passes the typed model.
- **Grep gate:** `git grep 'Mapping\[str, JsonValue\]' src/infrastructure/connectors/lastfm/conversions.py` returns nothing when done.
- **Layer flow:** unchanged.
- **Green:** `uv run pytest` stays green; no test weakened to pass.
- **Ratchet:** this removes a cluster of `reportUnknown*`-style dynamic access; check warning-count delta.
- **Scope discipline:** `LastFMTrackInfo` (the attrs container, lines 28-91) is healthy — don't rework it.

## Notes / counter-proposal

Audit disposition: the conversions family is otherwise **healthy** — the seed's "conversions ×3" is inherent per-service mapping, not duplication. Spotify's and MusicBrainz's modules need nothing beyond the local-import nit.
