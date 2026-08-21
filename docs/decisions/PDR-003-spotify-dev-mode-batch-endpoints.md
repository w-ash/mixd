# PDR-003: Spotify Dev-Mode Batch-Endpoint Exposure

**Status**: Open — watching (2026-08-08); no action while the Feb-2026 endpoint restrictions stay postponed

**Question**: What does mixd do if Spotify's postponed Feb-2026 Development-Mode endpoint restrictions take effect — removing the batch fetch endpoints (`GET /v1/tracks?ids=`, `/artists?ids=`, `/albums?ids=`) the import fast path depends on?

**Why it matters / blast radius**: v0.10.2.4's import throughput rests on batched track lookups (~46k requests → ~920 for a full GDPR set). Live-probed 2026-08-08: `GET /v1/tracks?ids=` returns 200 for this app today. If the restriction lands and the app remains in dev mode, `get_tracks_by_ids` degrades to per-id calls — the fast path's chunking helper survives (singles are its degenerate case) but throughput reverts toward the ~155-minute probe ceiling, and search `limit` drops 50→10 (the fallback path already requests 10, so that part is absorbed). No data-correctness impact; wall-clock and rate-limit exposure only.

**Decide-by trigger**: (a) Spotify announces a date for lifting the postponement (was 2026-03-09, indefinitely deferred as of 2026-08), or (b) any `GET /v1/tracks?ids=` call starts returning 403 in prod logs — the batch client should log that distinctly, or (c) mixd ever needs >5 users (the dev-mode user cap bites first anyway).

**Do-nothing default**: Keep dev mode; the batch path keeps working while the postponement holds. Acceptable indefinitely for a single-user instance.

## Options when triggered

| Option | Cost | Notes |
|---|---|---|
| Apply for extended quota mode | Application effort + review wait; needs a real app description | **Closed 2026-08-20** — see ledger: extended quota requires an organization with ≥250k MAU |
| Accept per-id fallback | Zero code (batch helper already degrades) | Import throughput reverts; fine for incremental imports, painful for any future full re-import/rebuild-with-refetch |
| Cache-first hardening | Ledger + connector_tracks already make re-imports API-free | Full imports are one-time; after the first, the API dependency is mostly dead IDs and new releases |

## Evidence ledger

| Date | Type | Source | What it shows | Strength/caveat |
|---|---|---|---|---|
| 2026-08-08 | [S] | Live authenticated probe (this app's prod token) | `GET /v1/tracks?ids=` returns 200 with correct positional payload | Direct measurement; today's truth only |
| 2026-08-08 | [S] | developer.spotify.com Feb-2026 migration guide | Dev-mode apps: batch fetches removed, search limit 50→10, `/me/library` consolidation; existing-app enforcement postponed 2026-03-09 | Official; postponement has no announced end date |
| 2026-08-08 | [D] | v0.10.2.4 measurements | Batch path ≈ 920 calls vs ~46k per-id for the 200k set; per-id at 12/s ≈ 65 min of pure probe time | The size of what's at stake |
| 2026-08-20 | [S] | developer.spotify.com changelog, 2026-07-23 | 25 Client IDs per account restored, with one API quota pooled across all of a developer account's apps — a second Client ID adds no capacity, so per-tenant app registration is not a quota mitigation | Official; `re-verify` |
| 2026-08-20 | [S] | developer.spotify.com blog 2026-06-18 (refresh-token expiration); enforced for existing apps 2026-07-20 | Refresh tokens expire 6 months after the original authorization; refreshing does not extend the window; expiry surfaces as 400 `invalid_grant` | Official; `re-verify` |
| 2026-08-20 | [S] | developer.spotify.com blog 2025-04-15 (extended-access criteria) | Extended quota is granted only to organizations with ≥250k MAU — the "apply for extended quota" option in this PDR is closed for mixd | Official; criteria could change — `re-verify` |

All Spotify program-terms rows are perishable — `re-verify` on read.

## What would change my mind

- **Toward applying for extended quota now**: any 403 on a batch endpoint in prod; Spotify announcing an enforcement date; a second real user (the 5-user dev cap becomes the binding constraint before the endpoint one).
- **Toward not caring at all**: the first full import completes and the archive/ledger makes re-fetch-at-scale permanently unnecessary — measure how many API lookups the *second* year of operation actually needs.

## Scale ladder

~1 user (now): dev mode + postponement suffices. ~5 users: dev-mode user cap binds — extended quota becomes mandatory regardless of endpoints (see PDR-002's activation rings). Beyond: extended quota assumed; this PDR closes.

## History

- 2026-08-08 — Opened during v0.10.2.4 (batched-lookup fast path shipped; live probe confirmed availability; audit flagged the postponement as the only architectural threat to batch-first).
- 2026-08-08 — v0.10.2.8: trigger (b) instrumentation is live — the batch client (`spotify/client.py`, `_get_tracks_batch_impl`) now logs a distinct error naming this PDR on any 403 from `GET /tracks?ids=`, so the trigger is observable in prod logs rather than folded into the anonymous `unanswered` bucket.
- 2026-08-20 — Evidence appended from the 2026-08 provider research pass; the extended-quota option marked closed (orgs ≥250k MAU); v0.11.2 schedules the QUOTA_EXCEEDED 429 discriminator, sharpening trigger (b)'s observability.
