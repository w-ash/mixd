-- Identity-resolution quantification pack (memo §8, v0.8.18 baseline).
--
-- 13 read-only queries (Q1-Q13) sizing the failure modes catalogued in
-- docs/backlog/identity-resolution-design-space.md §1. Run before the
-- v0.8.18 repairs for the baseline and after deploy for the delta
-- (Q6/Q7 should freeze; Q8/Q8b should collapse to composite-only).
--
-- Usage:
--   psql "$PROD_DATABASE_URL" -v user_id='<USER_ID>' -f scripts/sql/identity-quantification.sql
--
-- The user_id variable is REQUIRED for non-bypass roles: tracks,
-- track_mappings, match_reviews, track_plays, and connector_plays are
-- FORCE-RLS'd (policy: user_id = current_setting('app.user_id', TRUE)) —
-- without it they return zero rows. connector_tracks is global (no RLS).
--
-- CAVEAT (verified 2026-07-03): the Neon `neondb_owner` role has BYPASSRLS,
-- which FORCE RLS does not override — run as that role, these queries see
-- ALL users' rows regardless of app.user_id. Interpret results as global,
-- or run as a non-bypass role for true per-user scoping.
--
-- Every statement runs read-only: line 1 makes all subsequent transactions
-- in this session reject writes at the server.

SET default_transaction_read_only = on;
SET app.user_id = :'user_id';

\echo '=== Sanity: scope of this run ==='
SELECT
    current_setting('app.user_id', TRUE) AS app_user_id,
    (SELECT count(*) FROM tracks) AS tracks,
    (SELECT count(*) FROM track_mappings) AS track_mappings,
    (SELECT count(*) FROM connector_tracks) AS connector_tracks_global,
    (SELECT count(*) FROM match_reviews) AS match_reviews,
    (SELECT count(*) FROM connector_plays) AS connector_plays;

\echo ''
\echo '=== Q1: method x confidence-band matrix (FM1e distribution check) ==='
-- Bands mirror the evaluation thresholds: reject <50, review 50-84,
-- accept 85-99, and exact-100 isolated (the FM1a inflation band).
SELECT
    match_method,
    connector_name,
    count(*) AS total,
    count(*) FILTER (WHERE confidence < 50) AS band_reject,
    count(*) FILTER (WHERE confidence BETWEEN 50 AND 84) AS band_review,
    count(*) FILTER (WHERE confidence BETWEEN 85 AND 99) AS band_accept,
    count(*) FILTER (WHERE confidence = 100) AS band_certain
FROM track_mappings
GROUP BY match_method, connector_name
ORDER BY connector_name, total DESC;

\echo ''
\echo '=== Q2: confidence histogram per connector, 5-point buckets (trimodality check) ==='
SELECT
    connector_name,
    (confidence / 5) * 5 AS bucket_floor,
    count(*) AS mappings
FROM track_mappings
GROUP BY connector_name, bucket_floor
ORDER BY connector_name, bucket_floor;

\echo ''
\echo '=== Q3: review-queue depth and age (FM5d) ==='
SELECT
    status,
    count(*) AS reviews,
    min(created_at) AS oldest,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY (now() - created_at)) AS median_age,
    max(created_at) AS newest,
    count(*) FILTER (WHERE created_at < now() - interval '30 days') AS older_than_30d
FROM match_reviews
GROUP BY status
ORDER BY status;

\echo ''
\echo '=== Q4: match_method vocabulary census (FM6b drift) ==='
SELECT match_method, connector_name, count(*) AS mappings
FROM track_mappings
GROUP BY match_method, connector_name
ORDER BY match_method, connector_name;

\echo ''
\echo '=== Q5: ISRC collisions in the global connector cache (FM2 scale) ==='
-- Multi-identifier ISRCs, split by whether the duration spread crosses the
-- 10s suspect threshold (SUSPECT_DURATION_DIFF_MS, isrc_validation.py).
WITH isrc_groups AS (
    SELECT
        isrc,
        count(DISTINCT (connector_name, connector_track_identifier)) AS identifiers,
        count(DISTINCT connector_name) AS connectors,
        max(duration_ms) - min(duration_ms) AS duration_spread_ms
    FROM connector_tracks
    WHERE isrc IS NOT NULL
    GROUP BY isrc
    HAVING count(DISTINCT (connector_name, connector_track_identifier)) > 1
)
SELECT
    count(*) AS multi_identifier_isrcs,
    count(*) FILTER (WHERE duration_spread_ms > 10000) AS suspect_spread_over_10s,
    count(*) FILTER (WHERE connectors = 1) AS within_single_connector,
    max(duration_spread_ms) AS worst_spread_ms
FROM isrc_groups;

\echo ''
\echo '=== Q6: bumped-confidence count (FM1a live corruption) ==='
-- confidence overwritten to 100 while the stored evidence remembers the
-- real engine score. NULL evidence = constant-assigned, excluded naturally.
SELECT
    match_method,
    connector_name,
    count(*) AS bumped,
    round(avg((confidence_evidence ->> 'final_score')::numeric), 1) AS avg_true_score,
    min((confidence_evidence ->> 'final_score')::numeric) AS min_true_score
FROM track_mappings
WHERE confidence = 100
  AND (confidence_evidence ->> 'final_score')::numeric < 100
GROUP BY match_method, connector_name
ORDER BY bumped DESC;

\echo ''
\echo '=== Q7: stale denormalized spotify_id (FM4d bug evidence) ==='
SELECT
    count(*) FILTER (WHERE t.spotify_id IS DISTINCT FROM ct.connector_track_identifier)
        AS column_disagrees_with_primary,
    count(*) FILTER (WHERE t.spotify_id IS NOT NULL AND m.id IS NULL)
        AS column_set_but_no_spotify_mapping
FROM tracks t
LEFT JOIN track_mappings m
    ON m.track_id = t.id AND m.connector_name = 'spotify' AND m.is_primary
LEFT JOIN connector_tracks ct ON ct.id = m.connector_track_id
WHERE t.spotify_id IS NOT NULL OR m.id IS NOT NULL;

\echo ''
\echo '=== Q8: Last.fm identifier-scheme split (FM3a fragmentation) ==='
SELECT
    CASE
        WHEN connector_track_identifier LIKE 'http%' THEN 'url'
        WHEN connector_track_identifier LIKE 'lastfm:%' THEN 'prefixed'
        WHEN connector_track_identifier ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
            THEN 'mbid'
        WHEN connector_track_identifier LIKE '%::%' THEN 'composite'
        ELSE 'other'
    END AS scheme,
    count(*) AS rows
FROM connector_tracks
WHERE connector_name = 'lastfm'
GROUP BY scheme
ORDER BY rows DESC;

\echo ''
\echo '=== Q8b: composite rows whose key differs from recomputed strip+lower (fold workload) ==='
SELECT count(*) AS case_variant_rows
FROM connector_tracks
WHERE connector_name = 'lastfm'
  AND connector_track_identifier LIKE '%::%'
  AND connector_track_identifier
      IS DISTINCT FROM lower(btrim(artists -> 'names' ->> 0)) || '::' || lower(btrim(title));

\echo ''
\echo '=== Q9: Last.fm mappings-per-track distribution (dual-mapping stock) ==='
SELECT lastfm_mappings, count(*) AS tracks
FROM (
    SELECT track_id, count(*) AS lastfm_mappings
    FROM track_mappings
    WHERE connector_name = 'lastfm'
    GROUP BY track_id
) per_track
GROUP BY lastfm_mappings
ORDER BY lastfm_mappings;

\echo ''
\echo '=== Q10: skeletal/dangling canonical estimate (FM3b stock) + orphaned connector rows ==='
SELECT
    (SELECT count(*)
     FROM tracks t
     WHERE t.duration_ms IS NULL AND t.album IS NULL
       AND EXISTS (SELECT 1 FROM track_mappings m
                   WHERE m.track_id = t.id AND m.connector_name = 'lastfm')
       AND NOT EXISTS (SELECT 1 FROM track_mappings m
                       WHERE m.track_id = t.id AND m.connector_name <> 'lastfm')
    ) AS lastfm_only_skeletal_canonicals,
    (SELECT count(*)
     FROM connector_tracks ct
     WHERE NOT EXISTS (SELECT 1 FROM track_mappings m
                       WHERE m.connector_track_id = ct.id)
    ) AS connector_tracks_unmapped_for_this_user;

\echo ''
\echo '=== Q11: unresolved-play backlog ==='
SELECT
    connector_name,
    count(*) FILTER (WHERE resolved_track_id IS NULL) AS unresolved,
    count(*) AS total,
    min(played_at) FILTER (WHERE resolved_track_id IS NULL) AS oldest_unresolved
FROM connector_plays
GROUP BY connector_name
ORDER BY connector_name;

\echo ''
\echo '=== Q12: duplicate-canonical estimate (FM3 scale) ==='
WITH dupe_groups AS (
    SELECT title_normalized, artist_normalized,
           count(*) AS canonicals,
           count(DISTINCT isrc) FILTER (WHERE isrc IS NOT NULL) AS distinct_isrcs
    FROM tracks
    WHERE title_normalized IS NOT NULL AND artist_normalized IS NOT NULL
    GROUP BY title_normalized, artist_normalized
    HAVING count(*) > 1
)
SELECT
    count(*) AS duplicate_groups,
    sum(canonicals) AS tracks_in_duplicate_groups,
    count(*) FILTER (WHERE distinct_isrcs > 1) AS groups_with_differing_isrc,
    count(*) FILTER (WHERE distinct_isrcs <= 1) AS groups_same_or_no_isrc
FROM dupe_groups;

\echo ''
\echo '=== Q13: confidence-constant fingerprint (FM1f — which hardcoded values dominate) ==='
SELECT
    confidence,
    match_method,
    count(*) AS mappings,
    count(*) FILTER (WHERE confidence_evidence IS NULL) AS constant_assigned_no_evidence
FROM track_mappings
WHERE confidence IN (90, 95, 100)
GROUP BY confidence, match_method
ORDER BY confidence DESC, mappings DESC;
