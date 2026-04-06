# UUID Type Migration & Review Candidate Persistence

## Completed: UUID Type Migration (2026-04-04)

**Goal:** Fix 106 basedpyright type errors from incomplete `int` → `UUID` migration for track IDs and workflow run IDs, plus ~15 unrelated type errors discovered in the same audit.

**Context:** The database and domain entities already use UUID, but many function signatures, dict type annotations, and helper parameters still reference `int`. The callers already pass UUID — the errors are at the receiving end.

**Strategy:** Fix root-cause definitions (function signatures, type aliases, Protocol methods), not callers.

### What was fixed

| Category | Count | Scope |
|----------|-------|-------|
| `int` → `UUID` in function signatures, return types, dict annotations | ~85 | ~25 files across all layers |
| Wrong attribute names (`last_synced_at`, `effective_direction`, `unchanged_count`) | 6 | `playlist_commands.py` |
| Private function used externally (`_fetch_spotify_display_name`) | 3 | `connector_status.py`, `auth.py`, `connector_commands.py` |
| Dead `UUID == None` guards removed | 5 | `delete_canonical_playlist.py`, `update_canonical_playlist.py`, `lastfm/operations.py`, `track/connector.py`, `workflow/runs.py` |
| `PlaySortBy` type narrowing, missing annotations, `rowcount` suppression | 6 | misc |

### What was deferred

The `_persist_review_candidates` method in `match_and_identify_tracks.py` was found to be fundamentally broken — it cast `match.connector_id` (a base62 Spotify ID string) to `int` and assigned it to a UUID FK column. Rather than half-fix it, the method was replaced with a log-only stub. See below.

---

## Remaining: Review Candidate Persistence

**Goal:** Restore MatchReview persistence in `_persist_review_candidates` — currently deferred because `connector_track_id` (FK to `connector_tracks.id`) requires connector_tracks rows to exist before MatchReview rows can be inserted.

**Impact:** Medium-confidence matches that fall between auto-accept and auto-reject thresholds are logged but lost. Users cannot review ambiguous matches via `mixd reviews resolve`. This only affects connectors where matches score in the review zone — most matches are auto-accepted or auto-rejected.

## The Problem

`MatchReview.connector_track_id` is a UUID foreign key to `connector_tracks.id`. When a match enters the review zone:

1. The `MatchResult` only carries `connector_id: str` (the external service ID, e.g. Spotify base62)
2. No `connector_tracks` row exists yet — those are created during `persist_identity_mappings` (the **accepted** match path only)
3. The review path needs connector_tracks rows to exist **before** creating MatchReview records

The accepted-match path solves this in `TrackConnectorRepository.map_tracks_to_connectors()` (lines 406-431 of `src/infrastructure/persistence/repositories/track/connector.py`):
- Bulk upserts connector_tracks with `lookup_keys=["connector_name", "connector_track_identifier"]`
- Builds `(connector_name, connector_id_str) → UUID` lookup map from returned rows
- Uses those UUIDs when creating track_mappings

## Stories

- [x] **Add `ensure_connector_tracks` to ConnectorRepositoryProtocol**
    - Effort: S
    - What: Add a protocol method that bulk-upserts connector_tracks rows and returns a `(connector_name, external_id) → UUID` map
    - Why: The review persistence path needs connector_tracks rows to exist before creating MatchReview records. Using the existing `ConnectorRepositoryProtocol` facade (not exposing `ConnectorTrackRepository` directly) keeps persistence format details out of the application layer.
    - Dependencies: None
    - Status: Completed (2026-04-04)
    - Notes:
        - Protocol: `src/domain/repositories/interfaces.py` (`ConnectorRepositoryProtocol`, ~line 560)
        - Implementation: `src/infrastructure/persistence/repositories/track/connector.py` (`TrackConnectorRepository`)
        - Signature: `ensure_connector_tracks(connector_name, tracks_data) -> dict[tuple[str, str], UUID]`
        - Each dict in `tracks_data`: `connector_id`, `title`, `artists` (list[str]), plus optional `album`, `duration_ms`, `isrc`, `release_date`, `raw_metadata`
        - Implementation builds persistence-format dicts internally (JSON `{"names": [...]}` for artists, `last_updated` timestamp) and calls `self.connector_repo.bulk_upsert()` with `lookup_keys=["connector_name", "connector_track_identifier"]`
        - Follows existing pattern from `map_tracks_to_connectors()` lines 406-416

- [x] **Implement review candidate persistence in use case**
    - Effort: M
    - What: Replace `_persist_review_candidates` log-only stub with two-phase implementation: ensure connector_tracks exist, then batch-insert MatchReview records
    - Why: Restores review persistence with correct FK references, matching the pattern used by `ResolveMatchReviewUseCase`
    - Dependencies: Add `ensure_connector_tracks` to ConnectorRepositoryProtocol
    - Status: Completed (2026-04-04)
    - Notes:
        - Phase 1: Build `tracks_data` dicts from `MatchResult.service_data` + `MatchResult.connector_id`, call `connector_repo.ensure_connector_tracks()`
        - Phase 2: Build `MatchReview` entities using returned UUID map, call `review_repo.create_reviews_batch()`
        - `match_weight` comes from `match.evidence.match_weight` (default 0.0 when evidence is None)
        - When a review is later **accepted**, `ResolveMatchReviewUseCase` calls `map_track_to_connector()` which upserts the same connector_tracks row (idempotent via lookup_keys) — no duplication
        - Remove the log-only stub, add `from src.domain.entities.match_review import MatchReview`
        - Also clean up `hasattr` dead code in `track_identity_service_impl.py:162`

## Verification

```bash
uv run basedpyright src/              # 0 errors
uv run pytest tests/ -x              # Green
uv run pytest tests/ -x -k "match_review or review_candidate"  # Targeted

# Manual
mixd workflow run <workflow-with-medium-confidence-matches>
mixd reviews list                     # Should show pending reviews
mixd reviews resolve                  # Interactive review should work
```
