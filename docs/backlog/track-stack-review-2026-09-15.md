# Track Stack Review — what to build once before artists and albums

**Status**: Active (2026-09-15; P1, P6, P2 shipped 2026-09-16 as pre-flight 1–3) — evidence record for the [v0.12.1 Pre-Flight Refactors](v0.12.x.md#pre-flight-refactors-hygiene--skimmers-may-skip) (P3–P5 still scheduled there) and the [v1.0.0 clean-break deletions](v1.0.x.md#v100-maturity-gate) (P7). Two independent reviews of the v0.12.x plan at git `2c3ec05d` (a plan review against the pinned stack, then a git-archaeology pass over the track identity substrate); the proposals are drained into those specs, this file keeps the *why* with commit SHAs. Delete when the v0.12.1 pre-flight ships and P7 is either done or re-filed
**Reviewed**: `src/infrastructure/persistence/repositories/track/*`, `database/db_models.py` track tables, `domain/entities/track.py`, `domain/matching/*`, `services/resolution_recorder.py`, the inward resolvers, `alembic/versions/045, 049, 051`, `fly.toml`. "Measured" = grep/awk/git output; opinion is labelled.

---

## 1. Verdict

1. The prior plan review's "≈2,900 lines of supersession machinery" was wrong by 5×: supersession is ≈540 src lines (`connector.py:401-672` + `live_rows.py` + two helpers), exercised in prod, and stays. The bulk of `connector.py` is **ingest orchestration living in a repository** (582 lines) plus primary election (387 lines) — that is what artists would inherit by copying.
2. The mapper's healing write is load-bearing for exactly one writer (`persist_identity_mappings` asserts with `primary=False`); a two-line change to `map_tracks_to_connectors` makes it dead.
3. The find-or-create decision is made at 7 sites with 3 different gates; `match_method` literals at 31 sites in 12 files.
4. `TrackLike.is_liked = False` is never written anywhere in `src/` — the tombstone is a presence row wearing a boolean.
5. Typed mapping tables, one generic mapping repository, one keyset-sort declaration, one application-layer resolution service; supersession as a per-table capability, off for artists/albums.

## 2. Archaeology — why each piece is there

| Piece | Origin (commit · version · incident) | Still load-bearing? | Essential to |
|---|---|---|---|
| Supersession chain (5 cols, 2 partial-unique, 2 partial idx, CHECK; `db_models.py:392-484`) | `b7b59a7e` 2026-07-27 v0.10.2 — FM4e "no supersession, no history" + FM4d 366 stale prod rows (memo §10.1); hardened `12707c2d`, `2fa8aacd` (v0.10.2.1); RESTRICT in 051 after audit C8 | **Yes.** Every mapping write funnels through `assert_mappings` (`connector.py:1573-1590`); 5 supersession events in prod at audit time. `next_verify_at`: no reader. `supersession_scope`: only ever written `None` (`resolution_recorder.py:252`) | **Tracks** — id relink/death and re-scoring (memo §10.2-10.3); artist ids are treated as permanent (memo §7) |
| Read-path healing write (`mapper.py:33-49, 176-183, 219-260`; dispatch `base_repo.py:77-86`, `mappers.py:64-77`) | `7e31ab05` 2025-08-15 "auto-healing"; rewritten `20613555` 2026-07-03 v0.8.18 C6 (FM4b/FM4c) | **For one writer only.** Every other path re-ensures a primary (supersession → `_restore_superseded_primaries:973`; merge `core.py:1391-1412`; unlink/relink; ingest `:1672`; review-accept). The hole: `ConnectorMappingSpec.primary` defaults `False` and `TrackIdentityServiceImpl.persist_identity_mappings` (`:174-186`) never sets it. The promotion emits **no resolution event** and fires mid-ingest via `get_by_id` (`connector.py:1381`) | **Neither** — guards a writer bug |
| `last_seen_at` | `fc5458d4` 2026-07-03 v0.8.18 FM1a | Yes (`_touch_last_seen:1470`) | Mapping in general — keep for artists/albums |
| `TrackLike.is_liked` / `last_synced` | `89fdcc3d` 2025-07-15 (oldest surviving piece) | **No.** Zero `is_liked=False` writers in `src/`; `sync_likes.py:338,735` always write `True`; `get_unsynced_likes:169` compares a value that is always `True` | Dead state |
| Twin sort registries | `TRACK_SORT_COLUMNS` `f4c500f9` 2026-03-18 (v0.5.1 keyset); `_SORT_SPECS` `1191128e` 2026-06-11 (DDD moves split them; importlinter forbids infra → application, `pyproject.toml:492-499`) | Yes, both consumed; plus a **third** registry `PlaySortBy` (`domain/repositories/play.py:111`, if-chains `plays.py:321-386`) and keyset written 3× (`core.py:1082`, `match_review.py`, `tags.py`) | Mapping in general |
| Track-typed `MatchProvider` (`protocols.py:29`) | `8f63c3b6` 2025-07-30 | Yes, 5 providers | Tracks (title/duration/ISRC evidence) |
| `ingest_external_tracks_bulk` (`connector.py:1116`) | `89fdcc3d` 2025-07-15 — original import path, predates the matching engine | Yes: `sync_likes:300`, playlist processing `:299,495`, repo→repo from `playlist/core.py:186` | The orchestration is mapping-general; its home in a repository is the accident |
| `_IdentityReuseIndex` / `_plan_identity_reuse` (`:138-176, 1300-1470`) | `82bd5716` 2026-08-10 v0.10.3.1 — audit C4, 55 twin groups | Yes | Mapping in general; but calls `create_evaluation_service()` **inside a repository** (`:1427-1433`) |
| `_resolve_ingest_isrc` + `queue_isrc_collision_review(s)` (`:1528, 2034-2216`) | v0.8.18 epic 3 FM2a (7 suspect ISRCs) | Yes | Tracks (ISRC); repository writes `match_reviews` — a use-case decision |
| `save_tracks` (`core.py:624`) | v0.10.2.4 throughput — 26 ms RTT floor (v0.10.x.md:566) | Yes, 2 callers | Mapping in general |
| `acquire_user_track_ingest_lock` (`ingest_lock.py`) | `dd29e855` 2026-09-03 v0.11.6 — 55P03 between workflow ingest and play-import | Yes | General; keyed on user, artist ingest takes it as-is |
| Merge CTE supersession (`core.py:1280-1513`, raw SQL, `conflation`) | v0.10.2 / v0.10.3.2 | Yes | A **second supersession writer** bypassing `assert_mappings` |
| `ConnectorTrackMapping` entity (`track.py:191-208`) | pre-2025-08 | **Dead** — constructed only in `tests/unit/domain/test_track_operations.py` (5 sites); FM6b | — |
| `MatchMethod` / `MappingOrigin` / `DenormalizedTrackColumns` in `config/constants.py:350` | FM1f | Yes | Domain rules in config — violates `domain-purity.md` |

## 3. Duplication and coupling (measured)

- **Canonical-track creators (5 entry points)**: `save_track:517` (upsert ladder ISRC→MBID→spotify_id, `:552-580`), `save_tracks:624`, `ingest_external_tracks_bulk → _create_track_with_mapping_row:1483 → save_track`, `unlink_connector_track.py:130`, `playlist/core.py:197`.
- **Mapping writers**: one seam (`assert_mappings:413`) reached from 3 methods, plus the merge CTE and `recorder.retire_mapping`. Primary election: 5 public spellings over one `_promote_primaries_by_uuid:2424` (387 lines, `:2288-2675`).
- **Connector-track writers**: 4, all through `build_connector_track_row` since `12707c2d`.
- **Find-or-create decision (7 sites, 3 gates)**: ingest (`:1300-1470,1528`: ISRC-owner + suspect, then name-key + `describes_same_recording` + evaluator); `save_track:552` (ISRC suspect only); `_shared/inward_track_resolver.py:358,758`; `spotify/inward_resolver.py:447,762-863`; `cross_discovery.py:317-329` (ISRC only); `apple_music:159`, `tidal:262` (ISRC only). The inward-resolver base is its own 1,006-line planner.
- **Literals**: `match_method` set at 31 sites / 12 files; confidence constants at 15 sites; `"direct"` / `100` hard-coded at `connector.py:1522-1523` — the source of Q13's 91% constant-confidence mappings.
- **Call graph**: likes-sync and playlist import → `ingest_external_tracks_bulk` (repo) → probes ISRC + name owners → per group: reuse (evaluator call inside repo) or `save_track` → `assert_mappings` → `_record_assertion` (repo decides `event_type`) → elect primaries. Play import → inward resolver → `save_tracks` → `map_tracks_to_connectors` → same seam. Match pipeline → `TrackIdentityServiceImpl` → `map_tracks_to_connectors` with `primary=False` → **no election** → mapper heals on next read. Merge → raw CTE → recorder events.
- **Layer-rule violations**: repository evaluates matches (`:1427`); repository queues reviews (`:2034`); repository orchestrates a repository (`playlist/core.py:186`); mapper performs UPDATEs (`mapper.py:219`); repository chooses event semantics (`:1601-1604`); domain constants in `config/`.
- **Size**: `connector.py` 2,938 = mappers/helpers 399 · assert/supersede 271 · map/restore 444 · ingest+reuse+events 582 · crud/chain 336 · reviews 254 · primaries 387 · diagnostics 264. `core.py` 1,797 = list/sort/facets 402 · merge/move 380. The domain protocol an artist repository would mirror has 35 methods (`domain/repositories/connector.py`).
- **Reusable seams already present**: `BaseRepository.bulk_upsert / upsert / _deduplicate_batch` (`base_repo.py:569-925`), `SimpleMapperFactory` (`mappers.py:151`), `build_connector_track_row`, `live_only()` / `expire_mapping_identity`, `ResolutionRecorderProtocol` (no FKs on events — takes `entity_kind` for free), `evaluation_service.should_accept / should_review` (read only `confidence`), `probabilistic.calculate_match_weight / weight_to_confidence`, `recording_identity.identity_key`, `ingest_lock`, `PageCursor`, `PaginatedResponse[T]`.

## 4. Where the proposals landed

| # | Proposal | Effort | Scheduled as |
|---|---|---|---|
| P1 | Delete the read-path healing write (fix the writer first) | S | v0.12.1 pre-flight 1 |
| P6 | Domain-typed match constants; delete `ConnectorTrackMapping` | XS | v0.12.1 pre-flight 2 |
| P2 | One `KeysetSort` declaration, paging lifted into `BaseRepository` | S | v0.12.1 pre-flight 3 |
| P5 | Likes (and favorites) as presence rows | S | v0.12.1 pre-flight 5 |
| P3 | Resolve-and-record service: pure planner + application orchestrator; repositories only persist | L | v0.12.1 pre-flight 6 |
| P4 | Three typed mapping tables, one generic `MappingRepository`; supersession per-table | M | v0.12.1 pre-flight 8 |
| P7 | Drop `tracks.spotify_id` / `mbid` + unique constraints; one supersession writer; drop reader-less columns | L | v1.0.0 gate (fresh database) — **partial ship**: the columns and their unique constraints dropped in v0.12.0.2 (migration 059); the supersession writer and the remaining reader-less columns (`supersession_scope`, `next_verify_at`) are still open |

Plan-review findings folded into the entity epics: the existing `Artist(name)` value object becomes `ArtistCredit`; no `artist_relations` table (unscheduled); album membership and ordering on `connector_tracks`, no `album_tracks`; one set-based migration on plain DDL (migrations 049/051 precedent; the release command runs over the pooler URL, so `autocommit_block` is unavailable; prod is ~82k tracks, not the 13k July baseline); `query_library(entity=…)` instead of per-entity read tools; `is_various` on `albums` only.

## 5. Left alone, and why

- `assert_mappings` two-statement shape, deferred self-FK, 23505 retry (`:413-672`): PG17-verified (memo §10.7), 1,031-line test file, exercised in prod. Generalise, do not redesign.
- `resolution_events` / `resolution_negatives`: FK-free by design; add nullable `entity_kind`.
- `last_seen_at`, the FM2a suspect gate, `_touch_last_seen`: the confidence-integrity fixes still hold (Q6 = 0).
- `ingest_lock.py`: a real 55P03 incident; already entity-agnostic.
- Denormalised play aggregates on `tracks` (v0.10.4): a measured hot-sort decision, unrelated to identity.
- Track-typed evidence (`algorithms.py`, `probabilistic.py`): generalise the provider protocol and the zoner, not the evidence.
- SQLAlchemy pin 2.0.51 is correct — 2.1 is at rc2 (2026-09-08); "2.1 best practices" references in the memo and `mapper.py:301` are premature.
