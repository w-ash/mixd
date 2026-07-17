-- Multi-source play-import convergence investigation pack.
--
-- Read-only queries (groups A-G) sizing the play-dedup failure modes for
-- docs/backlog/play-import-convergence-findings.md, which feeds the
-- v0.10.0 (Convergent Play History) and v0.10.1 (Continuous Play Polling)
-- backlog sections. Companion to identity-quantification.sql (track
-- identity); this pack covers *play/event* identity.
--
-- PROVENANCE: the findings doc's 2026-07-16 numbers came from a SQLite
-- adaptation of these queries against data/db/mixd.db — the only store
-- holding play data (prod had zero play rows, findings §1). Running this
-- file against prod returns empty results until the first at-scale import.
--
-- Groups:
--   A. census & resolution funnel        E. existing track_plays failures
--   B. cross-source timestamp deltas     F. per-field source trust
--   C. match-judgment weakness           G. coverage gaps by day
--   D. resolution divergence
--
-- Usage:
--   psql "$PROD_DATABASE_URL" -v user_id='<USER_ID>' -f scripts/sql/play-import-investigation.sql
--
-- Use the DIRECT (non -pooler) Neon endpoint: PgBouncer transaction mode
-- drops the session SETs below.
--
-- CAVEAT (verified 2026-07-03, PDR-002): the Neon `neondb_owner` role has
-- BYPASSRLS, which FORCE RLS does not override — every query below carries
-- an explicit user_id predicate instead of relying on RLS scoping.
--
-- Every statement runs read-only: the first SET makes all subsequent
-- transactions in this session reject writes at the server.

-- Scratch temp tables shared by B1/B2 and D2/D2b, created BEFORE the
-- read-only SET: read-only transactions reject all CREATE/DROP commands
-- but allow DML on temp tables. Session-scoped — dropped at disconnect.
CREATE TEMP TABLE pairs (delta_s double precision);
CREATE TEMP TABLE divergent_pairs (
  lf_title varchar, sp_title varchar, artists varchar,
  lf_played_at timestamptz, sp_played_at timestamptz,
  lf_track_id uuid, sp_track_id uuid
);

SET default_transaction_read_only = on;
SET statement_timeout = '120s';
SET app.user_id = :'user_id';

\echo '=== Sanity: scope + role posture ==='
SELECT
    current_user,
    (SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user) AS bypassrls,
    current_setting('app.user_id', TRUE) AS app_user_id,
    (SELECT count(*) FROM track_plays WHERE user_id = :'user_id') AS track_plays,
    (SELECT count(*) FROM connector_plays WHERE user_id = :'user_id') AS connector_plays;

\echo ''
\echo '=== A0: connector_track_identifier format sample (verify before trusting) ==='
SELECT connector_name, connector_track_identifier, played_at, ms_played
FROM connector_plays
WHERE user_id = :'user_id'
ORDER BY connector_name, played_at DESC
LIMIT 6;

\echo ''
\echo '=== A1: census per layer x source (rows, range, ms_played coverage) ==='
SELECT 'track_plays' AS layer, service AS src, import_source,
       count(*) AS rows,
       min(played_at) AS first_play, max(played_at) AS last_play,
       count(*) FILTER (WHERE ms_played IS NULL) AS ms_null,
       count(*) FILTER (WHERE ms_played = 0) AS ms_zero
FROM track_plays WHERE user_id = :'user_id'
GROUP BY 2, 3
UNION ALL
SELECT 'connector_plays', connector_name, import_source, count(*),
       min(played_at), max(played_at),
       count(*) FILTER (WHERE ms_played IS NULL),
       count(*) FILTER (WHERE ms_played = 0)
FROM connector_plays WHERE user_id = :'user_id'
GROUP BY 2, 3
ORDER BY 1, 2, 3;

\echo ''
\echo '=== A2: resolution funnel (unresolved ledger rows are invisible to dedup) ==='
-- cf. identity-quantification.sql Q11 (same funnel); kept so the pack runs standalone.
SELECT connector_name,
       count(*) AS total,
       count(*) FILTER (WHERE resolved_track_id IS NULL) AS unresolved,
       round(100.0 * count(*) FILTER (WHERE resolved_track_id IS NULL) / count(*), 2) AS unresolved_pct
FROM connector_plays WHERE user_id = :'user_id'
GROUP BY 1;

\echo ''
\echo '=== A3: import batch history (re-import / arrival-order context) ==='
SELECT service, import_source, import_batch_id,
       count(*) AS plays,
       min(played_at) AS range_start, max(played_at) AS range_end,
       min(import_timestamp) AS imported_at
FROM track_plays WHERE user_id = :'user_id'
GROUP BY 1, 2, 3
ORDER BY imported_at
LIMIT 100;

-- ---------------------------------------------------------------------------
-- Groups B-D measure on connector_plays via resolved_track_id.
-- CONTINGENCY: if A2 shows resolved_track_id is all-NULL (write-back was
-- never implemented — expected), use the *_tp variants that join through
-- track_plays / tracks instead. Both variants are included below.
-- ---------------------------------------------------------------------------

\echo ''
\echo '=== B1: cross-source |delta-start| histogram, 15s buckets (track_plays variant) ==='
-- lastfm played_at = start; spotify played_at = end -> normalize sp to start.
-- delta_s ~ 0 validates the end-vs-start model; a mode near +duration falsifies it.
-- Fills the session temp table (created in the preamble) once for B1+B2.
INSERT INTO pairs
SELECT DISTINCT ON (lf.id)
       extract(epoch FROM lf.played_at
               - (sp.played_at - make_interval(secs => coalesce(sp.ms_played, 0) / 1000.0))
              ) AS delta_s
FROM track_plays lf
JOIN track_plays sp
  ON sp.user_id = lf.user_id
 AND sp.service = 'spotify'
 AND sp.track_id = lf.track_id
 AND sp.played_at BETWEEN lf.played_at - interval '10 minutes'
                      AND lf.played_at + interval '20 minutes'
WHERE lf.user_id = :'user_id' AND lf.service = 'lastfm'
ORDER BY lf.id,
         abs(extract(epoch FROM lf.played_at
             - (sp.played_at - make_interval(secs => coalesce(sp.ms_played, 0) / 1000.0))));

SELECT (floor(delta_s / 15) * 15)::int AS bucket_start_s, count(*) AS pairs
FROM pairs
WHERE delta_s BETWEEN -600 AND 600
GROUP BY 1 ORDER BY 1;

\echo ''
\echo '=== B2: delta summary stats over the same pair set ==='
SELECT count(*) AS pairs,
       percentile_cont(0.5)  WITHIN GROUP (ORDER BY abs(delta_s)) AS p50_abs_s,
       percentile_cont(0.9)  WITHIN GROUP (ORDER BY abs(delta_s)) AS p90_abs_s,
       percentile_cont(0.99) WITHIN GROUP (ORDER BY abs(delta_s)) AS p99_abs_s,
       count(*) FILTER (WHERE abs(delta_s) <= 30)  AS within_30s,
       count(*) FILTER (WHERE abs(delta_s) <= 180) AS within_180s
FROM pairs;

\echo ''
\echo '=== C1: back-to-back same-track gap distribution per source (ledger layer) ==='
-- Any gap <= tolerance means two candidate matches inside one window.
SELECT connector_name,
       count(*) AS gaps,
       count(*) FILTER (WHERE gap_s <= 30)  AS le_30s,
       count(*) FILTER (WHERE gap_s <= 180) AS le_180s,
       count(*) FILTER (WHERE gap_s <= 360) AS le_360s
FROM (
  SELECT connector_name,
         extract(epoch FROM played_at - lag(played_at) OVER (
           PARTITION BY connector_name, connector_track_identifier ORDER BY played_at)) AS gap_s
  FROM connector_plays WHERE user_id = :'user_id'
) g
WHERE gap_s IS NOT NULL
GROUP BY connector_name;

\echo ''
\echo '=== C2: lastfm plays with MULTIPLE spotify candidates in the 180s window (track_plays variant) ==='
-- The population where first-match-wins is a coin flip.
SELECT count(*) AS lf_plays_with_multi_candidates
FROM (
  SELECT lf.id
  FROM track_plays lf
  JOIN track_plays sp
    ON sp.user_id = lf.user_id AND sp.service = 'spotify'
   AND sp.track_id = lf.track_id
   AND lf.played_at BETWEEN
         (sp.played_at - make_interval(secs => coalesce(sp.ms_played, 0) / 1000.0)) - interval '180 seconds'
     AND (sp.played_at - make_interval(secs => coalesce(sp.ms_played, 0) / 1000.0)) + interval '180 seconds'
  WHERE lf.user_id = :'user_id' AND lf.service = 'lastfm'
  GROUP BY lf.id HAVING count(*) > 1
) x;

\echo ''
\echo '=== C3: identifier strength of PLAYED tracks ==='
SELECT tp.service,
       count(DISTINCT tp.track_id) AS played_tracks,
       count(DISTINCT tp.track_id) FILTER (WHERE t.spotify_id IS NOT NULL) AS with_spotify_id,
       count(DISTINCT tp.track_id) FILTER (WHERE t.isrc IS NOT NULL)       AS with_isrc,
       count(DISTINCT tp.track_id) FILTER (WHERE t.mbid IS NOT NULL)       AS with_mbid,
       count(DISTINCT tp.track_id) FILTER (
         WHERE t.spotify_id IS NULL AND t.isrc IS NULL AND t.mbid IS NULL) AS no_strong_identifier
FROM track_plays tp JOIN tracks t ON t.id = tp.track_id
WHERE tp.user_id = :'user_id'
GROUP BY 1;

\echo ''
\echo '=== D2/E2: cross-service pairs within 30s whose canonical tracks DIFFER but identities match ==='
-- Resolution divergence as it manifests in the user-facing table:
-- one listen -> two canonical plays on two duplicate canonical tracks.
-- Fills the session temp table (created in the preamble) once, so D2 (count)
-- and D2b (sample) share one divergence definition.
INSERT INTO divergent_pairs
SELECT tl.title AS lf_title, ts.title AS sp_title,
       tl.artists_text AS artists,
       lf.played_at AS lf_played_at, sp.played_at AS sp_played_at,
       lf.track_id AS lf_track_id, sp.track_id AS sp_track_id
FROM track_plays lf
JOIN track_plays sp
  ON sp.user_id = lf.user_id AND sp.service = 'spotify'
 AND lf.played_at BETWEEN
       (sp.played_at - make_interval(secs => coalesce(sp.ms_played, 0) / 1000.0)) - interval '30 seconds'
   AND (sp.played_at - make_interval(secs => coalesce(sp.ms_played, 0) / 1000.0)) + interval '30 seconds'
JOIN tracks ts ON ts.id = sp.track_id
JOIN tracks tl ON tl.id = lf.track_id
WHERE lf.user_id = :'user_id' AND lf.service = 'lastfm'
  AND lf.track_id <> sp.track_id
  AND tl.artist_normalized = ts.artist_normalized
  AND (tl.title_normalized = ts.title_normalized OR tl.title_stripped = ts.title_stripped);

SELECT count(*) AS divergent_dup_pairs FROM divergent_pairs;

\echo ''
\echo '=== D2b: sample of divergent pairs (for the findings doc) ==='
SELECT * FROM divergent_pairs
ORDER BY lf_played_at DESC
LIMIT 25;

\echo ''
\echo '=== D3: duplicate canonical tracks among PLAYED tracks (divergence precondition) ==='
-- Played-tracks narrowing of identity-quantification.sql Q12 (duplicate-canonical estimate).
SELECT count(*) AS dup_canonical_pairs_with_plays
FROM tracks t1
JOIN tracks t2 ON t2.user_id = t1.user_id AND t1.id < t2.id
             AND t2.artist_normalized = t1.artist_normalized
             AND t2.title_normalized  = t1.title_normalized
WHERE t1.user_id = :'user_id'
  AND EXISTS (SELECT 1 FROM track_plays p WHERE p.track_id = t1.id)
  AND EXISTS (SELECT 1 FROM track_plays p WHERE p.track_id = t2.id);

\echo ''
\echo '=== D4: mapping-coverage matrix for played canonical tracks ==='
SELECT bool_sp AS mapped_spotify, bool_lf AS mapped_lastfm, count(*) AS tracks
FROM (
  SELECT t.id,
         bool_or(m.connector_name = 'spotify') AS bool_sp,
         bool_or(m.connector_name = 'lastfm')  AS bool_lf
  FROM tracks t
  JOIN track_mappings m ON m.track_id = t.id AND m.user_id = t.user_id
  WHERE t.user_id = :'user_id'
    AND EXISTS (SELECT 1 FROM track_plays p WHERE p.track_id = t.id)
  GROUP BY t.id
) x GROUP BY 1, 2;

\echo ''
\echo '=== E1: surviving SAME-track cross-service dup pairs, 30s tight / 180s fallback (dedup should have merged) ==='
-- One pass over the 180s band; the 30s tight-tolerance count is a FILTER over it.
SELECT count(*) FILTER (WHERE abs(delta_s) <= 30) AS surviving_dup_pairs_30s,
       count(*)                                   AS surviving_dup_pairs_180s
FROM (
  SELECT extract(epoch FROM lf.played_at
         - (sp.played_at - make_interval(secs => coalesce(sp.ms_played, 0) / 1000.0))) AS delta_s
  FROM track_plays lf
  JOIN track_plays sp
    ON sp.user_id = lf.user_id AND sp.service = 'spotify' AND sp.track_id = lf.track_id
   AND lf.played_at BETWEEN
         (sp.played_at - make_interval(secs => coalesce(sp.ms_played, 0) / 1000.0)) - interval '180 seconds'
     AND (sp.played_at - make_interval(secs => coalesce(sp.ms_played, 0) / 1000.0)) + interval '180 seconds'
  WHERE lf.user_id = :'user_id' AND lf.service = 'lastfm'
) d;

\echo ''
\echo '=== E3: merge bookkeeping census (source_services / merged_from_*) ==='
SELECT service, source_services, count(*) AS rows
FROM track_plays WHERE user_id = :'user_id'
GROUP BY 1, 2 ORDER BY 3 DESC
LIMIT 20;

SELECT service,
       count(*) FILTER (WHERE context ? 'merged_from_lastfm')  AS merged_from_lastfm,
       count(*) FILTER (WHERE context ? 'merged_from_spotify') AS merged_from_spotify
FROM track_plays WHERE user_id = :'user_id' GROUP BY 1;

\echo ''
\echo '=== E3b: F1 smoking gun — lastfm-service rows claiming spotify contributed ==='
-- PREFERRED_SOURCE_ORDER puts spotify first; a lastfm row carrying spotify in
-- source_services means arrival order (not priority) picked the winner.
SELECT count(*) AS lastfm_won_over_spotify
FROM track_plays
WHERE user_id = :'user_id' AND service = 'lastfm'
  AND source_services @> ARRAY['spotify']::varchar[];

\echo ''
\echo '=== E4: exact same-service duplicates the NULL-ms_played constraint gap admits (F3) ==='
SELECT service, count(*) AS dup_groups, sum(n) - count(*) AS excess_rows
FROM (
  SELECT service, track_id, played_at, count(*) AS n
  FROM track_plays WHERE user_id = :'user_id'
  GROUP BY 1, 2, 3 HAVING count(*) > 1
) d GROUP BY 1;

\echo ''
\echo '=== E4b: same check on the ledger layer ==='
SELECT connector_name, count(*) AS dup_groups, sum(n) - count(*) AS excess_rows
FROM (
  SELECT connector_name, connector_track_identifier, played_at, count(*) AS n
  FROM connector_plays WHERE user_id = :'user_id'
  GROUP BY 1, 2, 3 HAVING count(*) > 1
) d GROUP BY 1;

\echo ''
\echo '=== E5: same-service near-dup gaps in track_plays (re-import jitter vs real restarts) ==='
SELECT service,
       count(*) FILTER (WHERE gap_s <= 30)               AS le_30s,
       count(*) FILTER (WHERE gap_s BETWEEN 31 AND 180)  AS s31_180
FROM (
  SELECT service, extract(epoch FROM played_at - lag(played_at) OVER (
           PARTITION BY service, track_id ORDER BY played_at)) AS gap_s
  FROM track_plays WHERE user_id = :'user_id'
) g WHERE gap_s IS NOT NULL GROUP BY 1;

\echo ''
\echo '=== F1: context key census per service (SAMPLED) ==='
SELECT service, k AS context_key, count(*) AS occurrences
FROM track_plays TABLESAMPLE SYSTEM (10)
CROSS JOIN LATERAL jsonb_object_keys(context) AS k
WHERE user_id = :'user_id'
GROUP BY 1, 2 ORDER BY 1, 3 DESC;

\echo ''
\echo '=== F2: behavioral-field present rates, spotify plays ==='
SELECT import_source, count(*) AS plays,
       round(100.0 * count(context->>'platform')       / count(*), 1) AS platform_pct,
       round(100.0 * count(context->>'shuffle')        / count(*), 1) AS shuffle_pct,
       round(100.0 * count(context->>'skipped')        / count(*), 1) AS skipped_pct,
       round(100.0 * count(context->>'reason_start')   / count(*), 1) AS reason_start_pct,
       round(100.0 * count(context->>'album_name')     / count(*), 1) AS album_pct,
       round(100.0 * count(ms_played)                  / count(*), 1) AS ms_played_pct
FROM track_plays
WHERE user_id = :'user_id' AND service = 'spotify'
GROUP BY 1;

\echo ''
\echo '=== F3: field present rates, lastfm plays ==='
SELECT import_source, count(*) AS plays,
       round(100.0 * count(context->>'album_name')       / count(*), 1) AS album_pct,
       round(100.0 * count(context->>'lastfm_track_url') / count(*), 1) AS track_url_pct,
       round(100.0 * count(context->>'mbid')             / count(*), 1) AS mbid_pct,
       round(100.0 * count(ms_played)                    / count(*), 1) AS ms_played_pct
FROM track_plays
WHERE user_id = :'user_id' AND service = 'lastfm'
GROUP BY 1;

\echo ''
\echo '=== F4: canonical-track field richness by mapping coverage ==='
SELECT CASE WHEN bool_sp AND bool_lf THEN 'both'
            WHEN bool_sp THEN 'spotify_only'
            ELSE 'lastfm_only' END AS mapped_via,
       count(*) AS tracks,
       round(100.0 * count(isrc)        / count(*), 1) AS isrc_pct,
       round(100.0 * count(duration_ms) / count(*), 1) AS duration_pct,
       round(100.0 * count(album)       / count(*), 1) AS album_pct,
       round(100.0 * count(mbid)        / count(*), 1) AS mbid_pct
FROM (
  SELECT t.id, t.isrc, t.duration_ms, t.album, t.mbid,
         bool_or(m.connector_name = 'spotify') AS bool_sp,
         bool_or(m.connector_name = 'lastfm')  AS bool_lf
  FROM tracks t JOIN track_mappings m ON m.track_id = t.id AND m.user_id = t.user_id
  WHERE t.user_id = :'user_id'
  GROUP BY t.id, t.isrc, t.duration_ms, t.album, t.mbid
) x GROUP BY 1;

\echo ''
\echo '=== G1: coverage by month x service (what would polling add?) ==='
SELECT date_trunc('month', played_at)::date AS month,
       count(*) FILTER (WHERE service = 'spotify') AS spotify,
       count(*) FILTER (WHERE service = 'lastfm')  AS lastfm
FROM track_plays WHERE user_id = :'user_id'
GROUP BY 1 ORDER BY 1;

\echo ''
\echo '=== G2: days with spotify activity but ZERO lastfm scrobbles (and vice versa) ==='
WITH days AS (
  SELECT date_trunc('day', played_at)::date AS d,
         count(*) FILTER (WHERE service = 'spotify') AS sp,
         count(*) FILTER (WHERE service = 'lastfm')  AS lf
  FROM track_plays WHERE user_id = :'user_id'
  GROUP BY 1
)
SELECT count(*) FILTER (WHERE sp > 0 AND lf = 0) AS spotify_only_days,
       count(*) FILTER (WHERE lf > 0 AND sp = 0) AS lastfm_only_days,
       count(*) FILTER (WHERE sp > 0 AND lf > 0) AS both_days
FROM days;
