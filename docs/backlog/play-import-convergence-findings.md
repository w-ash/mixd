# Play-Import Convergence Findings

**Status**: Active as reference (reviewed 2026-07-31) — the milestones it fed have shipped ([v0.10.0](v0.10.x.md#v0100-convergent-play-history) convergent play history, [v0.10.1](v0.10.x.md#v0101-continuous-play-polling) continuous polling). Retained in root because live readers still cite it: the [v0.10.3 audit](v0.10.x.md#v0103-post-import-data-integrity-audit) uses it as source material for extending its checklist, [PDR-001](../decisions/PDR-001-data-ownership-model.md) carries its §1 prod probe as an evidence row, and `alembic/versions/040_plays_nulls_not_distinct.py` + `src/domain/matching/play_projection.py` cite it by path. **§1's environment survey is a 2026-07-16 snapshot, not current state** — it recorded zero prod play rows, which v0.10.3 re-measures once the at-scale imports land. **§9 (2026-08-01) is the audit's pre-import baseline**, frozen as Neon branch `baseline/pre-import-2026-08-01` (kept until the v0.10.3 audit closes)
**Run**: 2026-07-16, git `c3773737`
**Environments probed** (all read-only):
- Production Neon (`neondb_owner`, `rolbypassrls = t` re-verified) — **zero play rows** (see §1)
- Local dev PostgreSQL (`mixd-postgres-1`) — empty entirely
- Local SQLite dev DB `data/db/mixd.db` (alembic `b2c3d4e5f6a7`, pre-`user_id` single-user schema, last written 2026-03-16) — the only environment with real cross-source play data
- GDPR export files `data/imports/Streaming_History_Audio_*.json` (13 files)

Query pack: `scripts/sql/play-import-investigation.sql` (PostgreSQL form; SQLite variant run for this report because the data lives there — see §1).

---

## 1. Data landscape — the meta-finding

| Store | track_plays | connector_plays | Notes |
|---|---|---|---|
| Production (Neon) | **0** | **0** | 12,640 tracks / 23,176 likes / 1 checkpoint (spotify likes, 2026-04-21) — play history has **never been imported to prod** |
| Local dev PostgreSQL | 0 | 0 | fresh container, no data at all |
| SQLite dev DB | **1,290** | **1,814** | real Spotify+Last.fm overlap, Nov 2024 + Feb 2025; the dataset behind every number below |
| GDPR export files | — | — | 198,252 raw records, 2011-07-19 → 2025-02-28 (§7) |
| Last.fm cloud | — | — | full scrobble history, never bulk-imported anywhere |

**Implication (largest single finding): no environment holds precious play data.** The full corpus (198k Spotify export records + full Last.fm history) has never been imported at scale. Consequences for story design:

- The "repair years of defective history" framing is wrong. There is nothing to repair in prod and only a 1,290-row testbed locally. The rebuild command remains valuable as the *replay* mechanism (Kappa), but **no data-preservation migration constraint exists** — clean-slate re-import through the fixed pipeline is a legitimate (and simpler) path, consistent with the project's "clean breaks" principle.
- The first at-scale import will be the *new* pipeline's first real exercise: the SQLite-era numbers below predict what the current code would do to 198k+ records (58% double-count rate on overlap windows, §5).
- The convergence properties must be right **before** the first big import, not retrofitted after.

## 2. Source census (SQLite dev DB)

`track_plays` (canonical):

| service | import_source | rows | range | ms_played NULL |
|---|---|---|---|---|
| lastfm | lastfm_api | 461 | 2024-01-01 → 2025-02-28 | 459 (99.6%) |
| spotify | spotify_export | 829 | 2017-11-12 → 2025-02-28 | 0 |

`connector_plays` (ledger): lastfm 710 + 1 test row; spotify 1,103. **`resolved_track_id` is NULL on 100% of ledger rows (1,814/1,814)** — write-back was never implemented (defect F5); the ledger cannot currently be projected.

Overlap: 21 days have both sources active, 2 spotify-only days, 2 lastfm-only days (G2). Monthly overlap concentrates in 2024-11 (310 sp / 64 lf) and 2025-02 (499 sp / 395 lf).

## 3. Timestamp alignment — the end-time/start-time model is validated

Nearest-neighbor pairing of each Last.fm play to Spotify plays of the same canonical track within −10/+20 min (324 pairs), delta = `lf.played_at − (sp.played_at − sp.ms_played)`:

| Measure | Normalized (start-time model) | Unnormalized (raw end-time) |
|---|---|---|
| mean signed delta | **−1.2s** | −239.7s (≈ track duration) |
| within 5s | **259 / 324 (79.9%)** | — |
| within 30s | 268 (82.7%) | 27 (8.3%) |
| within 180s | 290 (89.5%) | 76 (23.5%) |

Histogram mass sits in the [−15s, +15s) buckets (265 pairs). **Verdict: Spotify `ts` = end-of-play, Last.fm `uts` = start-of-play, and subtracting `ms_played` aligns them to ~±5s** — the domain model in `play_dedup.py` is empirically correct, and without normalization matching would fail ~92% of the time.

**Tolerance verdict**: 30s catches 82.7% of nearest pairs; widening to 180s adds ~22 pairs (6.8%), and that 30–180s band skews **negative** (18 negative vs ~7 positive) — consistent with mid-track pauses: paused wall-clock time inflates `end − ms_played` past the true start, while Last.fm stamps the true start. Recommendation: keep 30s as the tight tolerance; treat the 30–180s band as match-eligible **only when no closer candidate exists and the delta is negative** (pause-aware asymmetric fallback), or accept 180s symmetric fallback with one-to-one nearest assignment (C2 shows ambiguity is rare). The tail beyond 180s (34 pairs, 10.5%) is dominated by genuine distinct repeat plays — do not chase it.

## 4. Match-judgment weakness inventory

| Weakness | Measurement | Verdict |
|---|---|---|
| Repeat plays inside tolerance (ledger, same track back-to-back) | gaps ≤30s: lastfm 0, spotify 12; ≤180s: lastfm 4, spotify 31 (C1) | Real but rare (~3% of spotify gaps). One-to-one nearest assignment suffices; no probabilistic scoring needed for v1 |
| Last.fm plays with ≥2 Spotify candidates in the 180s window | **0** (C2) | First-match-wins hasn't misassigned *in this dataset*, but the guarantee should still be structural (greedy nearest), not luck |
| Played tracks with no strong identifier (no spotify_id/isrc/mbid) | lastfm-played: **177/279 (63%)**; spotify-played: 166/542 (31%) (C3) | Last.fm-sourced tracks are identity-weak as expected; MBID present on 0 played tracks (deliberately distrusted, LB-431) |

## 5. Resolution divergence + existing failures (the headline defect evidence)

### 5a. F1 double-count, measured

Of 461 Last.fm canonical plays, **267 (58%) are double-counted**: a same-track Spotify play exists within 180s, and **both rows claim `source_services = ["spotify","lastfm"]`** (E1's 180s count and E3b agree at 267). This is `play_dedup.py:194-215` — the "new play wins" branch inserts the enriched winner but never deletes the matched loser (`PlayDeduplicationResult` has no delete list).

The other arrival order worked: **519 Spotify rows carry `merged_from_lastfm`** (F5 census) — when Spotify was already present and the Last.fm scrobble arrived second, the existing-wins branch correctly suppressed the new row and enriched in place (2 lastfm rows even received `ms_played` backfill). **Same data, opposite orders, opposite outcomes: order-dependence is proven in the wild, and its failure mode inflates play counts by up to +58% on overlap windows.**

### 5b. F6 resolution divergence, measured, with root cause

- **191 cross-service pairs within ±30s resolve to *different* canonical track_ids** despite equal normalized artist+title (D2).
- **143 duplicate canonical-track pairs** exist among played tracks alone (D3).
- Sample (D2b) shows the root cause directly — Last.fm-created canonicals are **lowercased**, Spotify-created ones keep display casing:

  | lf canonical title | sp canonical title |
  |---|---|
  | striptease | Striptease |
  | autumn remains | Autumn Remains |
  | don't try this at home | Don't Try This At Home |
  | entropia | ENTROPIA |

  Hypothesis (verify in the epic): the Last.fm inward resolver creates canonical `Track` rows from its lowercased `artist::title` identifier parts instead of the API's display metadata, and/or fails to match the existing Spotify-created canonical before creating. This matches the mechanism [identity-resolution-design-space.md](identity-resolution-design-space.md) already pins as FM3a (lowercased `artist::title` secondary identifiers, `lastfm/inward_resolver.py:129-139`) and FM3b (duplicate-canonical splits) — these measurements quantify that taxonomy on play data. Either way: **play-level bridging on normalized identity is required as a safety net, and the durable fix belongs upstream in identity resolution.**

### 5c. F3 constraint gap — not yet triggered, still armed

Zero exact `(track_id, played_at)` duplicate groups in either layer (E4/E4b) — because no full re-import has ever been run. The NULL-`ms_played` hole in `uq_track_plays_deduplication`/`uq_connector_plays_deduplication` (NULL ≠ NULL, so ON CONFLICT never fires for Last.fm rows) remains a loaded gun for the first `mode=full` re-import. Fix before the first at-scale import.

## 6. Per-field trust matrix

Present-rates measured on canonical plays (F2/F3) + GDPR internals (§7):

| Field | spotify_export | spotify_api (docs) | lastfm_api | mixd/manual (planned) | Survivorship rule |
|---|---|---|---|---|---|
| played_at | 100%, end-time, second precision | per-play ISO ts (start-vs-end **uncalibrated** — v0.10.1 calibration test) | 100%, start-time, second precision | authored | normalized start time of highest-precision member |
| ms_played | 100% | absent | 0.4% (backfill artifacts only) | computed | first non-null by channel priority |
| album_name | 100% | via track object | 99.8% | known | winner's; others under merged_from |
| platform/country/reason/shuffle/skipped/offline/incognito | 100% | absent (context object only: playlist/album source) | absent | n/a | spotify_export exclusive |
| loved flag | absent | absent | present (extended=1) | n/a | lastfm exclusive |
| track identity | URI (strong) | URI (strong) | artist::title (weak, 63% of its tracks have no strong id) | resolved at write | strong-id channels outrank |
| MBID | absent | absent | 28.9% present, distrusted (LB-431) | n/a | never an identity anchor |

Channel priority confirmed: `spotify_export > spotify_api > mixd > lastfm`.

## 7. GDPR export internals

13 extended-history files, **198,252 records, 2011-07-19 → 2025-02-28, 100% `Z` (UTC)** — no timezone hazard. Account-data files (`StreamingHistory_music_*.json`, minute-precision, no URI) parse to zero records in the current importer; exclude them.

| Measure | Count | Meaning |
|---|---|---|
| Exact intra-export duplicates (ts, uri, ms) | **3,930 (2.0%)** | collapse safely via the ON CONFLICT key |
| Same (ts, uri), **different ms_played** | **266** | defeat the conflict key → two ledger rows per listen; a **same-channel collapse rule is needed** (open question 5: answered YES — group same-channel records with identical ts+uri, keep max ms_played) |
| ms_played < 30s | 55,800 (28.2%) | population the Spotify resolver's duration filter drops while Last.fm mostly won't scrobble either — but filter thresholds differ; per-source coverage asymmetry to expect |
| ms_played = 0 | 841 | skip noise |
| incognito | 498 | excluded by resolver policy |
| null URI (podcast/audiobook) | 10 | filtered by parser |

Export ends 2025-02-28: everything since exists only in Last.fm (and future polling). Yearly volume ~18–30k plays.

## 8. Implications — answers to the open questions

1. **Existing duplicate volume** (F1/F3 classes): 267 F1 double-counts in the 1,290-row testbed (58% of overlap-window lastfm plays); zero F3 exact dups (never triggered). But §1 changes the frame: **no precious data → the "repair" epic becomes a replay/rebuild mechanism + optional clean-slate re-import, not migration surgery.**
2. **|Δstart| distribution**: model validated (mean −1.2s, 80% within 5s). Keep 30s tight; 180s fallback justified as *nearest-only, one-to-one*; pause-skew explains most of the 30–180s band.
3. **Divergence rate**: 191 pairs / 143 dup-canonical pairs — **exact-normalized bridging captures the observed divergence entirely** (all samples differ only in casing/punctuation). Fellegi–Sunter escalation not needed for v1; fix the lowercasing upstream.
4. **Skip storms within tolerance**: rare (≤31 ledger gaps ≤180s, zero multi-candidate ambiguity) — greedy nearest one-to-one is sufficient and cheap insurance.
5. **GDPR ms_played drift**: real — 266 same-(ts,uri) pairs with differing ms_played. Add the same-channel collapse rule (identical ts+uri → one observation, max ms_played).
6. **Historical backfill resolvability**: moot in prod (no rows); SQLite testbed is disposable. Resolution write-back (F5) is still required for the projection, but there is **no API-cost backfill problem**.
7. **Readers of the flat context / merged_from_* shape**: the shape is preserved by the projection's context rule (winner flat + `merged_from_<channel>`), so no reader migration; verify with a grep at implementation time (only consumer found in this pass: none outside the dedup module itself).

**Net story-shaping conclusions**: (a) harden constraints and build the projection **before** the first at-scale import — that import is the real migration; (b) bridging on exact normalized identity is enough; (c) the poller (v0.10.1) closes the post-2025-02 gap that the export cannot cover, and PDR-001's ~50-play window makes cadence the safety variable, not matching.

## 9. Pre-import prod baseline (2026-08-01)

**Run**: 2026-08-01, against frozen Neon branch `baseline/pre-import-2026-08-01` (`br-dark-resonance-akzi04lz`, copy-on-write off production `br-dry-water-ak9zsove`; keep until the v0.10.3 audit closes). The same census against prod primary minutes earlier returned identical numbers — branch ≡ prod at freeze. Prod is at v0.10.2.1, alembic `046_resolution_events_mapping`.

**Re-run**: `DATABASE_URL=<branch-or-prod-url> uv run python scripts/audit_play_integrity.py --check census` (cross-tenant: `neondb_owner` holds `BYPASSRLS`), plus `MIXD_USER_ID=cfe9d062-577f-4f51-8813-47db89650793 mixd stats [--health|--matching]` / `mixd history checkpoints` for the user-scoped views.

**Census** (all users):

| table | rows | | table | rows |
|---|---|---|---|---|
| tracks | 12,990 | | track_plays | **52** |
| connector_tracks | 13,712 | | connector_plays | **54** |
| track_mappings | 14,539 (all live, 0 superseded) | | play_sources | 54 |
| track_likes | 23,176 | | match_reviews | 0 |
| playlists | 13 | | resolution_events | 0 |
| connector_playlists | 494 | | resolution_negatives | 0 |
| playlist_mappings | 13 | | sync_checkpoints | 2 |
| playlist_tracks | 2,542 | | track_preferences | 0 |

`connector_plays` by `import_source`: `spotify_api` 50 (2026-07-21 → 2026-07-27), `lastfm_api` 2 + `spotify_export` 2 (both 2024-11-05, `repro_*` test rows).

**Tenancy — the real user has zero plays; every play row on prod is mistenanted or test residue:**

| user_id | tracks | track_mappings | track_likes | track_plays | connector_plays |
|---|---|---|---|---|---|
| `cfe9d062-577f-4f51-8813-47db89650793` (real) | 12,531 | 13,656 | 23,176 | **0** | **0** |
| `default` | 457 | 883 | 0 | 50 | 50 |
| `repro_7d81986c`, `repro_d46f7460` | 1 each | 0 | 0 | 1 each | 2 each |

All 50 `spotify_api` poller plays landed under `default` — the v0.10.1 poller ran without `MIXD_USER_ID` (the group-D mistenancy class, pre-existing before any at-scale import). The audit must not attribute these to the imports.

**User-scoped stats** (`cfe9d062…`): 12,531 tracks / 11 playlists / 11,588 liked / 0 plays. The 23,176 `track_likes` rows are 11,588 × 2 services (mixd + spotify). Tracks by connector: spotify 12,486, lastfm 897.

**Integrity health**: overall **WARN pre-import** — 50 orphaned connector tracks (the poller's, matching the mistenanted plays), 1 duplicate track by fingerprint; all other checks pass with 0.

**Matching**: 13,656 mappings for the real user — spotify direct 12,759, lastfm artist-title 704, lastfm mbid 193; every mapping at confidence 100, all drift signals zero (queue empty, no negatives, no dead ids).

**Checkpoints**: spotify/likes last synced 2026-04-21; lastfm likes, lastfm plays, spotify plays all `Never` — confirming no play import has ever run for the real user.
