# Changelog

All notable changes to Mixd are documented here — newest first, one dated entry per ship.
Each entry leads with the user benefit; technical detail follows; the deep story lives in the
linked backlog version file. Versioning follows mixd's four-segment
`major.minor.feature.revision` scheme (`.claude/rules/version-management.md`), not strict
SemVer. Format inspired by [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.10.3.1] — 2026-08-10

**Closes the rest of what the audit found.** A listen you paused your way through now counts as one listen, a remaster stops becoming a second track no matter which door it came in by, and retired identity history can no longer be deleted out from under the log that explains it.

- **A song split across interruptions counts once, not zero times.** The listen threshold was applied to each fragment, so three 40% pieces of one song failed three times over — 64 listens vanished that way. Fragments are now consolidated into one listen before the threshold judges them, and separately, 66 plays that had been counted twice consolidate into 65. The rule that keeps repeat plays apart is the export's own "track finished" marker, measured rather than assumed: consecutive plays that follow a completed one sum to 1.79× the track's length, every other kind to under 1.14×.
- **Both doors that created duplicate tracks are now shut.** The playlist and likes importer had no reuse guard at all — a larger share of the duplicates than the path fixed in 0.10.3. Both now share one definition of "same recording" rather than two that could drift apart.
- **Deleting a track can no longer take its mapping history with it.** The database refuses (migration 051), and the application refuses first with an error naming what was at risk. This is the fence around the cleanup script that caused the loss.
- **A log file mixd can't open no longer takes the site down** — it warns, names the path and the reason, and keeps running on console output.
- New `scripts/find_duplicate_canonicals.py` reports which existing duplicate tracks are safe to merge (29) and which are genuinely different recordings that must not be (26). Read-only; it prints the commands rather than running them.

→ [details](docs/backlog/v0.10.x.md#post-deploy-revisions) · [findings](docs/backlog/v0.10.3-audit-findings.md)

## [0.10.3] — 2026-08-10

**Your listening history has been audited against itself, and it holds up.** The first at-scale import — fifteen years of Spotify history plus Last.fm — was checked adversarially: end-to-start normalization re-derived exactly across 132,199 plays, zero dropped observations, zero orphaned plays, zero duplicate canonical rows. Nine defects were found around the edges, and the ones that mattered are fixed.

- **Your history knows why a play didn't count.** 80,209 ledger rows had no canonical play and looked identical whether they were deliberate skips or tracks mixd failed to identify. Every one now records its reason — 79,659 skips, 499 private sessions, 51 genuine failures — and an import that discards a third of a file says so at the time instead of leaving it to be discovered a year later.
- **Spotify's live API was stamping the wrong end of a play.** Its timestamps mark where a play *ended*, not where it began, so live-polled plays landed a full track-length away from the export's and Last.fm's view of the same listen and never merged. Calibration confirmed it three independent ways; the correction rides as ledger data through the existing normalization seam, never as a synthetic listening time.
- **Duplicate rows in Spotify's own export no longer double-count.** The export ships the same listen twice with timestamps a second apart; mixd already caught the exact duplicates and now catches the jittered ones, with a bound that makes a false collapse arithmetically impossible rather than merely unlikely.
- **A remaster no longer splits into a second track.** Canonical reuse checked ISRC, which a remaster never shares with its original, so releases of one song spawned twins — 55 groups of them, still growing. Reuse now consults the identity it already has, with a duration guard so genuinely different recordings still get their own entry.
- **Retired identity history can no longer be deleted out from under its own audit log**, and the listen threshold is documented as the deliberate Last.fm-parity rule it is — with its one intentional divergence, so a short track played start to finish still counts.
- Two findings dissolved under scrutiny rather than being fixed: a "lost listening session" was 28 private-session plays working exactly as designed, and six "split" listens turned out to be one tenancy bug wearing an identity costume.
- The audit harness is re-runnable (`scripts/audit_play_integrity.py`, five checks) so the next import and the next connector get measured, not assumed.

→ [details](docs/backlog/v0.10.x.md#v0103-post-import-data-integrity-audit) · [findings](docs/backlog/v0.10.3-audit-findings.md)

## [0.10.2.14] — 2026-08-09

**Fixes the outage the previous release surfaced.** mixd.me stopped responding after the v0.10.2.13 deploy: the app was crashing at startup trying to open its log file in a directory it has no permission to write.

- **A flat environment variable no longer wipes the settings around it.** Flat keys (`FILE_LOG_LEVEL`) and nested ones (`LOGGING__LOG_FILE`) both feed the same config group, and the merge replaced the group rather than the one field — so the container's log-file path was discarded and fell back to a relative default under a directory owned by root. The merge is now per field; a flat key still wins the field it names.
- **Two earlier changes had to meet for this to fire**: the flat log-level was added to the deploy config in v0.10.2.11, and v0.10.2.12 made flat keys read the process environment (previously they came only from `.env` files, which the deployed container has none of). It is invisible on a development machine, where `.env` sets the flat log path and wins the same race.
- Pinned by a test that drives the settings validator directly — going through the normal settings load lets this repo's own `.env` mask the bug.

→ [details](docs/backlog/v0.10.x.md#post-deploy-revisions)

## [0.10.2.13] — 2026-08-09

**Check back on a running export and it tells you where it is.** Hand mixd thirteen streaming-history files and the queue drained them correctly, but the view of it went blank at exactly the moment you'd look — every time it moved to the next file. Status was reaching the browser over two uncoordinated channels: live progress attached to whichever file was running, and a separate two-second poll for which file that was. The page derived one from the other, so between files it was attached to nothing.

- **The export is now one thing, not thirteen.** The drain is a single operation with the files as its parts, so the page attaches once at upload and stays attached to the end. There is no hand-off to be caught in the middle of, which also fixes the vanished progress bar, the last file's result being wiped the instant it finished, dropped completion notices, and several seconds of "Connecting…" at every file boundary.
- **Overall progress is counted in files and only ever goes forward.** `Importing 4 of 13` advances when a file finishes and never blends in the running file's own percentage — the blend is what made it slide backwards each time the queue advanced. Time remaining appears once two files have finished, weighted by size rather than by count, since an export's files differ several-fold.
- **A file that is waiting says how long it will be waiting.** `Queued · #7 · starts in ~14 min`, rather than the same grey "Queued" it would have shown twenty-five minutes earlier.
- **A finished file keeps its numbers.** Plays imported and time taken now survive on the row — previously they arrived on a channel the page had already left, so a reload showed thirteen status words and none of the results. A reload mid-export restores the whole view, stream or no stream.
- **A failure is legible without being alarming.** The bad file moves to the top of the list with its reason; the rest of the export carries on and the header stays neutral until it has actually finished, because red at minute six of thirty reads as "the whole thing died".
- **The running file shows which stage it is in** — ingest, resolve, project — instead of a bar that only says something is happening. One export announces itself once when it is done, not once per file.

Under the hood this reuses the sub-operation machinery playlist imports already run on, so it needed no new endpoint and no new event types; progress routing simply learned to walk more than one level, which also fixes silently-dropped nesting in workflow runs. Follow-up review caught and fixed four issues in the same day, including a concurrency race and a cancelled export sitting out a 30-second window during shutdown.

→ [details](docs/backlog/v0.10.x.md#post-deploy-revisions)

## [0.10.2.12] — 2026-08-09

**Your most-played music stops failing to import.** The 2026-08-09 runs refused 55 plays with `track_resolution_failed` on Radiohead, Oasis, New Order and the Vince Guaraldi Christmas canon, and reported them as dead Spotify ids. Asking Spotify about all 47 of them settled it: **none are dead.** Forty are relinks — Spotify pointing an old id at a current one — and seven are simply alive. The search fallback that was supposed to rescue "dead" ids was never reachable, and never ran.

- **A relink now resolves onto the track your library already holds.** Spotify relinks a 2011 export id onto a remaster, and your library usually already has that remaster from the live sync — under a *different* ISRC, because the payload names the original recording and the canonical names the remaster. Reuse only ever checked the ISRC, so it missed a pairing Spotify had stated outright, tried to create a second canonical on an id already taken, and the write was refused. Reuse now checks the current id first: the provider's own assertion of identity outranks an ISRC match. Your 2011 plays and today's plays land on one track.
- **Two old ids relinking to the same track no longer poison the chunk.** Both `Wonderwall` ids point at the same remaster, as do both `Let Down` ids. Each emitted its own mapping row for the shared id, which the live-mapping constraint rejects — taking the whole chunk's bulk write with it, including every unrelated track in it. One mapping per id now.
- **A write the database refused stops looking like a dead identifier.** It left no mapping, no negative-cache row and no distinct count, so it was invisible in everything the run recorded — which is why this took a live API probe to diagnose rather than a query. Failed writes now record a `write_failed` event and report their own count, kept apart from genuinely unresolvable ids because the two want opposite responses: one is a retry, the other is a dead identifier.
- **The import run record stops dropping four of its own counters.** `suppressed`, `reused_tracks`, `degraded_persists` and the new `write_failed` were measured per chunk and logged, then discarded before the run was saved — so they were readable only while that machine's logs survived, and a run-level zero for a key that was merely absent read as evidence of health.
- **Exporting `DATABASE_URL` to point a command at your local database now works.** `.env.local` holds the production connection string and auto-loads for every command, so the documented way to run a script safely is to export first. That export was ignored: settings read dotenv content before the shell environment, inverting its own documented precedence, and a script you believed was local resolved to production. Found because the reproduction for this fix would otherwise have written canonical tracks and mappings to the live database.
- Verified read-only against production: 39 of the 47 failures are relinks onto a canonical already held, and resolve through the new path.

→ [details](docs/backlog/v0.10.x.md#post-deploy-revisions)

## [0.10.2.11] — 2026-08-09

**Imports get an instrument, and the two things it was always going to blame get fixed.** A creation-heavy chunk measured ~4.3s against a designed ~1.1s, and the resolution rate fell within a single run (851→543 plays/min) as the library grew. Investigating found the gap was never unattributed — the design's "~22-26 round trips per chunk" counted only one method's own statements, and the real figure is ~68.

- **Every import chunk now logs a `play_import_chunk` line** with wall time, database time, round-trip count, per-phase timings and a per-operation breakdown. It counts at SQLAlchemy's cursor-execute events, so it sees what a repository-level timer cannot: batched multi-row inserts, and each `SAVEPOINT`/`RELEASE` pair. `rtt_ms` on that line is what decides every future tuning question, and this line stays.
- **The round trip to the database is a floor, and that is now proven rather than assumed.** Measured from the running machine: 24.55ms per statement through Neon's pooler, 23.29ms bypassing it — so the connection pooler costs 1.26ms and the rest is simply San Jose to Oregon. Moving the app next to the database would have been the single biggest win here, but Fly has retired its Pacific Northwest region and every remaining North America region is *further* away. San Jose was already the best available. The consequence is recorded in `fly.toml` so it isn't re-investigated: the only lever left on import speed is issuing fewer statements.
- **The canonical-reuse lookup no longer grows with your whole library.** It built one condition group per input — about 250 OR'd predicates for a 50-play chunk — across two indexes that didn't lead with the user id, so its cost scaled with the entire table. It now joins against a two-parameter array probe, giving one cached query plan at any batch size, and both indexes were rebuilt user-first. That declining within-run rate was this.
- **Three round trips per chunk removed outright**: the transaction-begin hook issued two statements where one does (imports open a transaction per chunk), and the primary-election chain looked up connector ids the caller had just finished writing — twice. Suspect-ISRC reviews, previously ~7 statements *each*, are now one batch.
- **A latent bug fixed before it could bite**: the session factory registered its row-level-security hook without a guard, on an object shared by every factory ever built — a second one would have made every transaction in the app issue that statement twice.
- Imports now report `reused_tracks`, `suppressed` and `degraded_persists`, so a chunk's timings can be read against its shape, and the slow per-item write path finally says when it engaged.
- Three docstrings that documented the wrong mechanism — the statement inventory, and two separate claims about PostgreSQL's subtransaction limit — were corrected. They were the reason this work was scoped wrongly in the first place.

→ [details](docs/backlog/v0.10.x.md#post-deploy-revisions)

## [0.10.2.10] — 2026-08-08

**The upload actually shows its queue.** First real-world use of the multi-file import found the card frozen at the file-picker state while the queue drained fine server-side — two client bugs with one root: the queue view depended on an invalidate-triggered refetch of a query stuck in 404-error state (no queue existed before the first upload), and the file picker's displayed filenames were its own internal state, untouched when the page cleared its copy.

- The upload response now seeds the queue query cache directly (it is the same envelope the GET returns), so the per-file chips render the moment the POST succeeds; the invalidation still follows for freshness.
- The file picker remounts when a queue starts, clearing the stale filename list.
- The regression test exercises the true first-upload sequence (404 → upload → queue visible, picker reset) — the generated API mocks' always-200 default had hidden it.

→ [details](docs/backlog/v0.10.x.md#post-deploy-revisions)

## [0.10.2.9] — 2026-08-08

**Your imported history now belongs to you — and imports run in minutes, not hours.** The 2014-file re-import took 78 minutes and delivered tracks the app couldn't show: every canonical track the Spotify import created was silently written under the local-dev `default` tenant instead of your user (one missing argument, `create_track_from_spotify_data` never set `user_id`; the owner role's BYPASSRLS let the write through). One bug, two faces:

- **Integrity**: 7,852 mistenanted tracks on prod; 12,313 of the batch's plays resolved onto tracks your own row-level-security-scoped reads can't see. Fixed by threading the tenant through the resolver's creation path (keyword-only, no default — a caller that forgets fails loudly), and repaired by a reset-and-resolve script: null the affected plays' resolution pointers *first* (their track FK is ON DELETE CASCADE — delete-first would take the raw ledger with it), delete the mistenanted tracks, re-upload once under fixed code. The pre-existing 457-track poller-era residue is deliberately untouched (v0.10.3 audit's scope).
- **Performance**: user-scoped lookups never saw prior chunks' tracks, so every chunk re-created ~40 — and the identity-claim probe (scoped to the row's own wrong tenant) *did* see them, routing ~40 rows/chunk onto a silent per-item write path at ~8 round-trips each: the measured ~9s per 50-play chunk. Fixed, a creation-heavy chunk is ~1s and repeat chunks hit the mapping fast path in ~200ms — a 15k file in ~3–5 minutes, the remaining GDPR files in minutes each.
- **The silent path now announces itself**: `save_tracks` logs one warning per chunk when new rows fall back to per-item writes — its docstring claimed that set was empty in practice, and nothing would have said otherwise.
- **Excluded plays no longer cost anything**: incognito (private-session) plays are filtered before track resolution, so a chunk of them no longer fetches and creates ~47 tracks it then discards. Attribution note: a play both incognito and too-short now counts as `incognito_excluded` (privacy exclusion is categorical).
- ISRCs are stored normalized (uppercase, hyphen-free) — the raw form written before could never match the probes that search for it.
- The regression is now structurally caught: a real-Postgres test imports under a *non-default* tenant and asserts created tracks carry it and the second chunk hits the fast path — the assertion that would have failed all along.

A high-effort adversarial review of this wave confirmed ten findings, nine fixed before ship (the tenth — removing `Track.user_id`'s silent default entity-wide — is a scheduled follow-up in `unscheduled.md` § Tenancy hardening):

- **The repair script's pre-delete guard now sees every tenant the CASCADE sees**: one tenant-unpredicated still-pointing count refuses the delete and names any foreign tenants found — per-tenant predicates alone would have let a third tenant's raw ledger be cascade-deleted silently.
- **The startup orphan sweep runs to completion before the server accepts requests** — it could previously delete an upload directory mid-stream (files register with the queue only after streaming finishes).
- **The deploy busy gate closes its two blind windows**: an upload still streaming now counts as busy, and when the gate passes while stale >12h `running` rows were excluded as reaper-dead, the workflow says so instead of passing silently.
- **A Last.fm outage no longer mints permanent junk tracks**: the client's blanket error-suppression is overridden on the enrichment calls, so transport failures fail the identifier retryably; only a genuine "no such track" (error 6) degrades to raw names.
- Metrics honesty (an all-malformed-URI chunk reports its failures instead of reading clean), duplicate-filename upload rows render correctly, the upload route handler is back inside the 5–10-line rule, and the hand-rolled batching helper is replaced by `itertools.batched`.

→ [details](docs/backlog/v0.10.x.md#post-deploy-revisions)

## [0.10.2.8] — 2026-08-08

**A run that dies with its process now says so — and a deploy can no longer kill an import mid-run.** v0.10.2.5's shutdown hook covers kills the process observes (SIGTERM → drain → honest `error`); this closes the two it cannot: the unobservable kill (SIGKILL, OOM, machine loss) that leaves a phantom `running` row forever, and the self-inflicted one — a deploy replacing the machine while an import is in flight.

- **Startup liveness reaper**: any `operation_runs` row still `running` when the process boots and older than a 12-hour bound is marked `error` with a "process died" issue naming how long ago it started and that re-running is safe (re-imports are idempotent). No schema change — the heartbeat-column shape workflow runs use is deliberately deferred until unattended runs outgrow the startup-only guarantee.
- **Pre-deploy busy gate**: the release workflow polls the public `GET /health?busy=true` (active runs *plus* queued-not-started export files, which have no run row yet) every 30s for up to 30 minutes before `flyctl deploy`, then refuses with a clear message. Fail-open only when the app is unreachable — a down app can't be running an import and must never block its own fix — but fail-closed (`busy: true` with an error marker) when a serving app's probe errors, so a database blip can't wave a deploy through over a live import. The gate ignores rows past the reaper's own bound (those are dead by definition, and the deploy's restart is exactly what reaps them), counts via a bare SQL `count(*)` that can't be truncated behind the reaper's batch cap, and both queries get a partial index on running rows.
- **PDR-003 instrumentation rides along**: a 403 on the batch `/tracks?ids=` endpoint now logs distinctly (naming the PDR) instead of folding into the anonymous unanswered bucket — the watch trigger for Spotify's postponed dev-mode batch-endpoint removal is now observable.

→ [details](docs/backlog/v0.10.x.md#post-deploy-revisions)

## [0.10.2.7] — 2026-08-08

**The full Last.fm history import drops from a measured 4–6 hours to well under one.** The 2026-08 batch audit found nearly all of the cost was single-item work smuggled into batch contexts — one API call and ~45 sequential statements per track, one fetch per calendar day across a decade. Every seam already had a batch counterpart in the codebase; this release routes the Last.fm path through them.

- **Concurrent enrichment, planned writes, bulk persist**: the per-identifier getInfo→discover→write loop becomes three phases — API enrichment runs concurrently under the existing 4.5/s limiter, resolution decisions are planned as pure values, and each chunk persists via one bulk `save_tracks` + one `map_tracks_to_connectors` (the Spotify resolver's proven shape, now shared).
- **One persistence skeleton for every tolerated write loop**: savepoint-bulk with per-item fallback — where per-item is literally the bulk call on a one-element chunk — extracted to a single helper used by the Spotify resolver, the Last.fm resolver, and the canonical-reuse batch. Three hand-copies were the alternative.
- **7-day fetch windows**: one `user.getRecentTracks` call per calendar day was a mixd convention, not an API shape — the API takes arbitrary bounds at 200/page. Windows keep the v0.10.2.4 invariant verbatim (persist, then checkpoint, then commit — a crash loses at most one window and zero persisted rows).
- **A partial fetch now raises instead of silently truncating**: previously a failed page mid-pagination returned the rows gathered so far — indistinguishable from a quiet week, and the importer would checkpoint past the gap, losing the tail of the window forever. Review hardening closed the two remaining bypasses: a page failing response validation raises too (it used to end pagination as an empty success), and a window hitting the 10,000-record fetch ceiling subdivides into per-day fetches instead of warning and dropping its oldest records (a single day still ceilinging raises — that is no real account).
- **Rebuild projects only active days**: one `SELECT DISTINCT` of days holding resolved plays replaces tiling every empty day between the ledger's bounds — one stray 2011 row no longer costs ~5,000 empty-day round trips. The span-tiling path and its bounds query are deleted outright.

→ [details](docs/backlog/v0.10.x.md#post-deploy-revisions)

## [0.10.2.6] — 2026-08-08

**Hand mixd the whole GDPR export at once and walk away.** A Spotify export arrives as 13 streaming-history files, and until now the only way in was one at a time — pick, upload, watch, repeat: an evening of babysitting a progress bar. Now the upload takes them all, a server-side queue drains them one by one, and you come back to a per-file record of what landed.

- **The queue lives on the server, not the browser tab** — it survives reloads, sleeping laptops, and dropped connections. A reloaded tab re-attaches to the running file's live progress and the finished files' records.
- **Sequential by design, one concurrency slot for the whole drain**: parallel files would contend for the same write path (v0.10.2.4 measured ~80% of import time there) and race to create the same tracks. The queue occupies exactly one of the three operation slots start to finish, so a Last.fm import or workflow still runs alongside.
- **A failed file never stops the queue**: its run records status and issues like any other (each file is an ordinary run in Import History with its own counts), the queue moves on, and recovery is re-queueing one file — re-imports are zero-delta, so re-queueing anything you're unsure about costs nothing.
- **Bounded and honest about durability**: 25 files / 500MB per queue (the machine has no volume — queued uploads sit on ephemeral disk and die with a machine restart, alongside the queue that points at them; finished runs stand in Import History either way). Oversize rejects before anything lands; orphaned upload directories are swept at startup.
- **Cancel remaining** drops not-yet-started files; the running one finishes on its own terms.

→ [details](docs/backlog/v0.10.x.md#post-deploy-revisions)

## [0.10.2.5] — 2026-08-08

**A long import now survives a deploy, a restart, or the machine going away — and when one is killed anyway, it says so instead of reporting success.** v0.10.2.4 shipped the throughput work; this closes the incident that made that throughput unusable in practice, so the remaining GDPR files and the full Last.fm history can run unattended.

**The autostop incident (2026-08-08), diagnosed and closed.** A 54-minute prod import died at the finish line: Fly's proxy autostopped the machine running it ("excess capacity" — the proxy watches inbound traffic, not work inside the container), the cancellation escaped a too-narrow exception handler, and the dead run was durably recorded as *complete* with empty counts — 15,317 plays ingested, zero projected, "successful" on refresh. Fixes across every layer it touched:

- **Infra** (per Fly's own June-2026 long-running-tasks blueprint, which describes this incident class verbatim): `auto_stop_machines = 'off'`, SIGTERM with the maximum 300s drain window (was the default SIGINT + 5s — why the import died instantly), single-machine operation (`fly scale count 1` at deploy).
- **Honest cancellation**: `CancelledError` is caught before the generic handler, the run finalizes as `error` with a "cancelled by server shutdown" issue via a *shielded* audit write (verified load-bearing — removing the shield fails the test), and the app's shutdown hook drains background tasks inside the kill window so the path runs on a live event loop.
- **Kill loses at most one chunk**: `resolved_at` write-back moved from end-of-run (where the incident discarded all 15,317 resolutions) into each chunk's transaction; the end bracket keeps only the cheap, idempotent, day-scoped projection.
- **Re-upload heals stranded runs — proven**: an integration test reproduces the incident's exact stranded state and pins that re-uploading the same file resolves and projects everything with zero duplicate rows. The stale backfill script (obsolete detection premise; RLS silently pinning it to the wrong user) was repaired as the fallback for rows a re-upload can't reach.
- **The UI never lies about a dead stream**: one bounded SSE resume carrying `Last-Event-ID` (activating the server's replay filter, previously dead code), then polling the durable run row into the same terminal card/toast the stream would have shown; "Connection failed" now means the network is actually down, and an amber "Reconnecting" state covers the gap.

**One way to fetch a track, and a dead-code gate that means it.** The single-track fetch is gone entirely — callers pass lists (`get_tracks_batched([id])` for one), and the stale-ID diagnose script got the same batch-first treatment (50× fewer calls, and its hand-rolled classification no longer mistakes a failed request for a dead track). This also cleared a red `vulture` step that had been failing CI's dead-code gate since the v0.10.2.4 assembly.

Also: the `get_database_url` tests now assert the normalization contract against pinned inputs. They previously compared the result to the ambient settings value — which made them vacuous in CI (no `.env`, so both sides were the empty string) and failing on any machine whose `.env.local` carried a plain `postgresql://`.

Follow-ups scheduled in-series: multi-file import queue (v0.10.2.6), batch-first Last.fm path (v0.10.2.7), liveness reaper + pre-deploy busy gate (v0.10.2.8). PDR-003 opens the watch on Spotify's postponed dev-mode batch-endpoint removal — the one finding that threatens batch-first architecturally.

**A dependency-freshness sweep rides along.** Every dependency to its latest stable release — internal currency and security patches rather than anything user-visible, taken now because this release redeploys anyway.

- **Python** (22 packages): `cryptography` 49→50, `starlette` 1.3.1→1.6.0, `fastapi` 0.141.1, `websockets` 16→17. `pyproject.toml` floors now track the lock rather than the oldest version that once happened to work — mixd deploys from its own lockfile and is never co-installed as a library, so a tight floor costs nothing and the manifest stops overstating what it supports.
- **Web** (10 packages) plus pnpm 11.5.2 → 11.20.0 and Playwright 1.61.1 → 1.62.1, each moved across every pin at once (`packageManager`, `Dockerfile`, `ci.yml`, e2e docs). Baselines regenerated in the new Playwright image came back pixel-identical — which incidentally proves `elkjs` 0.12 lays the workflow graphs out exactly as 0.11 did.
- **GitHub Actions**: checkout / setup-node / upload-artifact v7, pnpm/action-setup v6, build-push v7, buildx v4. `setup-uv` v7 → **v9.0.0**, pinned to the full version because it stopped publishing major tags at v8.0.0 as a supply-chain measure — the usual `@v9` shorthand would have failed CI at "Unable to resolve action". Its one breaking change (`prune-cache` now defaults to false) raises Actions cache usage.
- Held back deliberately: `orval` 8.24.0 (published hours before the sweep, inside pnpm's `minimumReleaseAge` cooldown) and `pydantic-core` 2.48.0 (pydantic exact-pins 2.46.4).

→ [details](docs/backlog/v0.10.x.md#post-deploy-revisions)

## [0.10.2.4] — 2026-08-08

**The remaining imports run in a fraction of the time — and the Last.fm import can no longer lose data.** The first sizable GDPR file took ~21 minutes; live measurement on prod showed 80% was database round-trips creating new tracks (~45 sequential statements × 26ms each) and most of the rest was one-at-a-time Spotify probes. Extrapolated to the full 200k-record set, that was many hours. This release attacks both, plus three correctness finds from the same audit.

- **Batch track lookups**: `GET /v1/tracks?ids=` (verified live against this app — the Feb-2026 dev-mode restriction doesn't bind) replaces per-id probes: ~46k requests become ~920, preserving redirect detection via requested-vs-returned id correlation.
- **Bulk track creation**: new tracks persist per-chunk through the batched write path the likes import already uses, with automatic fallback to the per-item savepoint loop if a chunk's bulk write fails — the isolation guarantees stay, the ~50s-per-chunk DB tail goes.
- **Rate limit raised to 12 req/s with a shared 429 brake**: a rate-limited response now pauses the whole bucket for the server's stated window instead of only the one call that saw it.
- **Last.fm checkpoint integrity (fixed before the full-history import)**: the day loop checkpointed ahead of persistence — a crash mid-history would silently lose everything fetched before it while resuming past it. Plays now persist before their checkpoint advances ("checkpoint never leads persisted data").
- **Projection fixes**: progress events during the projection tail were all silently dropped (non-monotonic against the resolution phase's counter — frozen bar + warning storm); projection now scopes to days that actually contain resolutions, so one stray decade-old play can no longer trigger a 14-year empty-day sweep.
- Also: the redundant per-mapping existence check is gone (~one round trip per created track), and dead-ID duration evidence accumulates across chunks so the wrong-version veto keeps its strength on large files.

A high-effort adversarial review of this wave confirmed ten findings; all fixed before ship:

- **A failed batch request can't condemn its tracks.** The batch fetch now distinguishes *unanswered* (chunk request failed) from *dead* (Spotify said null) — previously one transient failure classified ~50 live tracks as dead, wrote month-long backoff, and cached wrong search-substituted mappings. Unanswered ids are simply retried next import.
- **The 429 brake is bounded and honest**: the pause is capped by the same per-service bound the retry sleep uses, the bucket restarts *empty* after a pause (resuming paced instead of releasing a synchronized burst into the window the server just closed), and pause extensions landing mid-sleep are honored.
- **Projection ownership is airtight**: day-scoping now reaches back far enough to own groups whose anchor a cross-channel neighbor pulled across midnight (a class a full rebuild could not heal — the chunk fetch window had the same off-by-tolerance gap and both now share one derived `_ANCHOR_REACH` constant), and day derivation is explicitly UTC.
- **Checkpoint-resume metrics tell the truth**: real inserted/duplicate counts flow from the ledger's ON CONFLICT through per-day persistence to the run summary — a resumed run reports its re-fetched rows as duplicates, not imports. The `uow`-less call shape that would have silently skipped persistence is no longer expressible.
- **One way to do each thing** (the review's DRY mandate): the per-item persist fallback is literally bulk-of-one per savepoint (~150-line second write path deleted), primary election is a single stack (primacy is a flag on the mapping spec — the parallel promotion list is gone), and the single-track fetch is the degenerate case of the batch call.

→ [details](docs/backlog/v0.10.x.md#post-deploy-revisions)

## [0.10.2.3] — 2026-08-08

**Dead Spotify IDs now actually get rescued.** The first prod retry under v0.10.2.2 did its job — the failed play showed its reason: "Robert Johnson – Come On in My Kitchen (track_resolution_failed)", classified dead. But the track is alive on Spotify under a new ID; the search fallback that should have found it was asking a malformed question. Spotify binds a field filter to a single term unless quoted, so `artist:Robert Johnson track:Come On in My Kitchen` searched for artist "Robert" and title "Come" — every multi-word artist or title got a garbage query, and at full-import scale that would have falsely condemned a large share of rescuable old IDs to a month-long retry snooze.

- **Field-filter values are quoted** (`artist:"Robert Johnson" track:"Come On in My Kitchen"`), with embedded quotes sanitized from raw export data; the fallback search now uses the full 10-result window (the Feb-2026 dev-mode cap) instead of 5.
- **One bounded widening retry**: if the precise quoted search clears nothing past the 0.7 title-similarity gate, a single plain free-text search runs through the same gate — paced by the connector rate limiter like every call.
- **Misses are loud**: a fallback that finds nothing logs at warning with the exact queries issued, so a malformed-query regression can never again be indistinguishable from a genuinely vanished track.
- **Guarantees pinned by test, not just design**: re-uploading the same export file is a zero-delta no-op (the v0.10.3 audit's re-import check, formalized early), and text search can only ever run as the last-resort fallback — never for an ID the API answered.
- Verified live before shipping: the corrected query returns the incident track as the #1 result against the real Spotify API.
- The one mis-condemned track self-heals: its ~1-day no-match backoff lapses, and the next import re-searches with the corrected query. No manual data edits.

**A mostly-successful import no longer masquerades as a total failure — and it names its casualties.**

- **New `partial` run status**: an import that did real work but recorded per-item errors shows an amber "Completed with issues" badge instead of the red Error badge (new status through domain → API → UI; migration `047` widens the status CHECK constraint — without it the write would have been rejected and the run stuck at `running`). The scheduler's failure signal is unchanged: partial still counts as a failure for scheduling health, and "Retry failed only" works on partial runs.
- **Every unresolved track is listed.** The per-item resolution failures (previously discarded after the first exemplar) are persisted as individual issues on the run — `{track, spotify_id, reason}` each — capped at 50 with an "…and N more, see server logs" notice. Expanding the Import History row now shows exactly which tracks couldn't resolve, written atomically with the run's status.

A same-day adversarial review of this release found 8 issues; all fixed before ship:

- **A rescued dead ID can't be mapped to the wrong recording.** Three independent guards: the full artist+title evaluation must pass; the candidate's artist must resemble the export's (a measured gap — the shipped matcher scores a same-title wrong-artist pair at 93/100, over the accept bar, because artist disagreement costs only ~7 points); and **duration evidence derived from your own listening**: for the ~93% of tracks with at least one played-to-completion play (`reason_end: "trackdone"`), the median completed `ms_played` ≈ the track's true duration — it feeds the matcher's duration scoring and vetoes any candidate ≥15s shorter than that estimate (a 3:38 cover can't substitute a track you played for 4:30). Median over completed plays only, because seeks can inflate or shrink a single play's `ms_played`.
- **Scheduled and overnight runs get the same truth as manual ones.** The result→status/issues mapping moved to the application layer (`operation_outcome.py`); nightly and demand-triggered imports now record partial status, real counts, and per-track issues — previously they collapsed to a bare red Error with "No issues recorded", and a *crashed* scheduled run recorded no reason at all (also fixed).
- **The live toast tells the truth**: a watched partial import shows an amber "completed with issues" toast with a View-log action instead of a green success; the duplicate second toast is gone (the surfaces now claim the shared toast ledger). The per-track failure list renders as legible rows, not raw JSON.
- Search hygiene: the free-text widening query gets the same quote sanitization as the filtered one; widening is per-caller opt-in (cross-discovery no longer doubles its miss-path search volume); playlist matching regains its lost recall via the same one-widening mechanism; and the warning log carries the query strings actually sent on the wire, not a parallel reconstruction.

→ [details](docs/backlog/v0.10.x.md#post-deploy-revisions)

## [0.10.2.2] — 2026-08-02

**When an import fails, mixd now tells you why — and one bad track no longer kills the whole batch.** The first GDPR history upload on prod died in 32 seconds showing only "errors: 1" and "No issues recorded". Two defects compounded: the importer converts pipeline exceptions into a soft-failure result whose message lives in a field the audit row never persisted (so the reason existed nowhere queryable), and prod logs revealed the failure itself was a poisoned-transaction cascade — the track resolvers' continue-on-error loops swallow a per-item SQL failure and keep issuing statements on the now-aborted transaction, so every later item dies with `InFailedSqlTransaction` and the run's recorded error is a misleading secondary symptom.

- **One bad item rolls back alone.** `UnitOfWorkProtocol.savepoint()` wraps each tolerated per-item write in the three resolver loops (canonical reuse, Spotify track creation, Last.fm track creation), so a failure rolls back to the savepoint and the batch genuinely continues — verified against real Postgres.
- **Failure reasons are durable.** The SSE seam appends the failure message to the run's `issues` on both failure paths (soft-failure results and uncaught exceptions), so the Import History row now shows the reason — the UI already renders issues, no frontend change.
- **`Retry-After` is honored.** All connector retry policies wait out a 429's server-stated window (+1s margin, capped at the policy's max wait) instead of hammering exponential backoff inside it (`RetryAfterWait` in `_shared/retry_policies.py`) — the resolver loops call the Spotify API mid-transaction, so an unhonored rate-limit window was burning retry budget exactly where it hurt.
- **Logs stop lying.** The import use case no longer logs "Successfully completed" while holding a soft-failure result, and the per-item handlers log with full tracebacks.
- `OperationResult.failure_message` is the single accessor for the soft-failure reason (`metadata["error"]` / `metadata["errors"]`).

Hardening for the first at-scale imports (research-driven, same release):

- **Resolution commits in chunks of 50.** Phase-2 resolution previously ran as one transaction for the whole batch — with per-item savepoints, a 200k-row import would blow past Postgres's 64-cached-subtransaction limit and degrade badly. The orchestrator now commits every 50 plays (`commit_batch`, idempotent upserts), which also bounds crash rework to one chunk.
- **Connectors are rate-limited client-side.** A hand-rolled monotonic-clock token bucket (`ConnectorRateLimiter`) paces every API call — including retry attempts — at the single `_api_call` choke point, seeded from the existing `rate_limit` settings; Spotify gets a conservative 5 req/s default. Prevents entering 429 windows instead of surviving them. (aiolimiter rejected as stale/pre-3.14; pyrate-limiter noted as the cross-process upgrade path.)
- **Bulk statements get a real time budget.** Import and rebuild operations run with `statement_timeout=300s` via transaction-local `set_config` riding the existing RLS hook — pooler-safe, auto-reverting to the 30s OLTP default at each commit.

→ [details](docs/backlog/v0.10.x.md#post-deploy-revisions)

## [0.10.2.1] — 2026-07-31

**Your pinned matches stay pinned, and the audit trail tells the whole truth.** A same-day adversarial code review of v0.10.2 found the one path where an automated re-match could silently overrule a primary mapping you chose, plus several places the new event log could disagree with the rows it explains. All confirmed findings are fixed, and the 2026-07-02 dependency audit closes with the layer contract now enforced in CI.

**The architecture now enforces its own rules.** Mixd's layer boundaries — Interface → Application → Domain ← Infrastructure — were documented but unchecked, and had quietly drifted: 22 direct imports crossed a boundary no rule sanctioned. Nothing users could see, but every one of them was a place where a future change ripples further than it should. All 22 are resolved and `lint-imports` now runs in CI, so the next one fails the build instead of accumulating.

- **18 eliminated, 4 legalized.** Fixes where an alternative already existed: user settings and the workflow-run lifecycle moved onto `execute_use_case`; chat pending-actions, the admin reset, play-history imports, and Spotify grant checks each grew a domain protocol and a UoW accessor rather than reaching for a session; `error_classification` turned out to be presentation, not persistence — nothing in `infrastructure/` imported it — and moved to `interface/_shared/`. Legalized with written rationale in `.claude/rules/`: the health probe's raw-engine read, the composition root's adapter teardown, `whoami`'s connector probe, and the startup OAuth-state prune (the last two are the existing v0.6.5 credential carve-out, which simply never named them).
- **Three claimed sanctions no rule granted** are gone — `play_poll_policy`, `import_play_history`, and `pending_actions` each documented themselves as an approved bridge that `application-patterns.md` never named. Two became ports; one became a repository. A docstring is not ratification, and CI now says so.
- **Latent defect found by the contract**: `WorkflowRunRepositoryProtocol` under-declared its own implementation by two parameters (`output_tracks`, `node_details`) that callers were passing. Invisible while callers used the concrete class directly; a type error the moment they went through the protocol.
- **`--no-cache` is not optional** in the CI step: a cached `lint-imports` run under-reported 18 edges against a tree that had 22, so a cache-hit green would have been indistinguishable from a real one.
- Also: `missing_from_grant` moved to domain (four readers across three layers), and an orphaned alembic revision (`down_revision = None`, referenced by nothing) was deleted — the last directory under `src/` that lacked an `__init__.py`, and so the last one invisible to the import graph.

**The v0.10.2 audit trail now tells the whole truth.** A post-ship code review of the mapping-supersession work found the event log could disagree with the rows it explains — and one path where an automated re-match could overrule a decision the user had pinned. All confirmed findings are fixed.

- **A re-match can no longer depose a primary you pinned — or any primary.** When a supersession moves a mapping onto a track that already has a live primary for that connector, restoration now fills a vacancy or does nothing, matching the merge's "flipping an existing primary is not our business" rule (same `NOT EXISTS` guard). Previously the destination's primary — including a `manual_override` pin — was silently demoted and `tracks.spotify_id` rewritten. The promote and denormalized-id sync also collapsed from per-row statements into batched `VALUES`-join UPDATEs inside the same transaction.
- **Retired mappings keep their own evidence.** The upsert wrote the successor's `confidence_evidence` onto the retired row, leaving history explained by a different decision's evidence; it now refreshes only on freshness touches.
- **Supersession events carry the real reason.** A manual relink wrote `supersession_reason='manual'` on the row but `'rematch'` into the event payload; the reason now rides `MappingAssertion` to the recorder.
- **One question, one `queued` event.** A still-pending match review refreshed by a re-import re-logged a `queued` calibration event at probability 1.0 on every import, biasing the queue-admission stratum; the pipeline now pre-checks which questions were already asked.
- **`matcher_version` covers all its levers.** Variation markers, text equivalences, the ISRC suspect-duration threshold, and ISRC-grade methods are now hashed (config fields via attrs introspection, so a new field can't escape), and the substrate/lever boundary is declared in the docstring. One-time consequence, per the documented contract: every stored hash changes once, so remembered cannot-link rejections are re-proposed one time.
- **Tenancy verified per row.** The play-import guard checked only the first row's `user_id`; it now scans the whole batch (O(n) string compares, O(1) memory) and names the first offender.
- **The agent can list what mixd stopped trying.** The rejections/dead-ids listing moved out of a CLI closure into `ListResolutionNegativesUseCase` — closing an interface-layer rule violation — and the parity gate then demanded agent coverage: `query_stats` gained a `negatives` view, the lookup `manage_track_matches` always instructed the model to do but no tool could serve.
- **`resolution_events` indexed for its one reader** (migration `046`): a partial `(user_id, resulting_mapping_id, recorded_at DESC)` index serves "why do we believe this mapping", and the unread `matcher_version` index is dropped from the hot insert path.
- DRY residue from the review: one connector-track row builder in `_shared` (was three copies, and its extraction retired the recorder's lazy-import workaround), one `default_metric_config()` home, one grant-string parser (`grant_scopes`) shared by `missing_from_grant` and a hoisted `TokenStorageGrantProvider`, a dead importer hook deleted, and the last test-file type suppression rewritten away.

→ [details](docs/backlog/v0.10.x.md#post-deploy-revisions)

## [0.10.2] — 2026-07-31

**Your library remembers why it believes things — and lets you take a decision back.** Until now an identity mapping was a single mutable row: a re-import could overwrite the link between a connector track and your canonical track, and nothing recorded that it had happened. v0.10.2 makes mappings append-only — a new resolution supersedes the old one instead of clobbering it — and writes every accept, reject, merge, and supersession to an immutable event log carrying the matcher version that made the call. So "why is this track linked to that one?" now has an answer, a wrong match you reject stays rejected instead of returning on the next import, and a rejection you made by mistake can be withdrawn.

What you can do now:
- **Reject a bad match once.** A rejected pair becomes a sticky cannot-link constraint — not re-proposed until the matcher version changes or one side's match-relevant metadata does, so the same wrong suggestion stops reappearing on every import.
- **Take a rejection back.** `mixd tracks unreject <connector-track> <candidate>` withdraws one, and the assistant can too (a fifth `unreject` operation on `manage_track_matches`, behind the same two-phase confirmation as the other four). Nothing is deleted — the withdrawal stamps `unrejected_at` and emits its own `unrejected` event recording which surface asked, so a reversal is as auditable as the rejection it reverses.
- **See what mixd has stopped trying.** `mixd tracks rejections` lists active cannot-link pairs; `mixd tracks dead-ids` lists connector IDs that have missed often enough to look dead, so you can relink them yourself.

How it works:
- **Supersession, not overwrite** (migration `044`). Mappings gain a live/superseded lifecycle with partial live indexes and a two-statement `assert_mappings` with bounded 23505 retry; a session-level live filter plus a src-wide per-statement `live_only()` conformance scan keeps every reader on the live set. Merge retires losers as `conflation` supersessions instead of deleting rows.
- **One write seam.** Every mapping mutation — relink, unlink, set-primary, delete, review-accept, merge — routes through a single recorder, so no mutation can outrun its event and the integrity rules live in one place. Denormalized `tracks.spotify_id` now sources from the primary live mapping only, fixing the redirect-flow ordering bug; 044's pre-pass repairs the 366 affected prod rows.
- **Death is reported, never acted on** (migration `045`). Nothing writes an `id_dead` supersession; repeated misses feed a `dead_id_candidates` counter and the CLI view. Providers relink far more often than they delete, and the asymmetry decides it — a track silently losing its link surfaces weeks later when a playlist comes up short, while a lingering stale mapping only costs quota.
- **Calibration-ready from birth.** Events record score, zone, and selection probability at queue time, because recall is unmeasurable later if those fields were never written.
- **Toolchain, same ship.** uv is pinned to one version across local/CI/production via `[tool.uv] required-version`; CI moved from `uv sync --frozen` to `--locked`, so lockfile drift fails on the PR instead of at deploy; the Dockerfile now builds on every PR (it was previously first built by `flyctl deploy` — *after* the release tag was published); Dependabot gained the `docker` ecosystem. `mcp` moved to stable v2.0.0, and the repo migrated off the unmaintained `encode/httpx` to Pydantic's `httpx2`, closing dependency-audit work order W4 and clearing 15 Starlette deprecation warnings.

→ [details](docs/backlog/v0.10.x.md#v0102-mapping-supersession--resolution-event-log)

## [0.10.1] — 2026-07-27

**Today's listening counts today.** Play history used to arrive only when you asked for it — a GDPR export that lands weeks late and stops the day you requested it, or a manual import you had to remember to run. v0.10.1 makes Spotify's recently-played API a live observer: it is polled on a cadence that adapts to how you actually listen, and refreshed on demand at the moments plays are read, so a workflow filtering on "played this week" sees this morning's plays. The same release stops the workflow surfaces lying about their own state — a save is visible the moment it lands, and a run is visible wherever you happen to be watching.

What you can do now:
- **Connect once and forget it.** Re-authorize Spotify (the grant now covers listening history) and the poller starts itself; a grant missing the scope surfaces as "re-connect needed" on the connector card without breaking likes or playlists. An on/off switch and the live cadence sit on the Sync page.
- **See fresh counts where you consume them.** Opening the dashboard or a track page, asking the assistant over MCP, or running a workflow that reads play history all refresh in the background first — reads never block on it, but a *scheduled* workflow waits (bounded, then proceeds), because that is where stale counts would silently corrupt output with nobody watching.
- **Know when something was missed.** Spotify keeps only your 50 most recent plays. If a poll finds that window saturated, the checkpoint says so and points at the fix (connect Last.fm as a second observer) instead of failing quietly.

How it works:
- **One execution path, three triggers.** The scheduler heartbeat, demand reads, and workflow pre-runs all go through a shared `run_sync_target`, so a demand-triggered poll produces the same `OperationRun`, checkpoint update, and run-log entry as a scheduled one — with `initiated_by` + a new `trigger_detail` recording *why* it fired. Extracted from the scheduler's `_dispatch_sync`, which is now a thin adapter (and no longer mis-records scheduled syncs as `"manual"`).
- **Cadence follows window fullness, not emptiness.** The poll response measures the user's own fill rate: an empty window stretches the interval toward a redundancy-aware cap (2h when this is the only live observer, 24h when Last.fm is also scrobbling), a near-full one halves it, and a saturated one floors it at 15 minutes and raises a possible-gap warning. One time constant cannot serve both album listening (~3.3h to fill 50 slots) and skip-heavy listening (~37 min).
- **Backoff is a cost mechanism, not just a politeness one.** The poller writes its effective interval back to `schedules.interval_minutes` (migration `043`, a third cadence arm beside daily/weekly), so a scale-to-zero database sleeps through a stretched interval instead of being woken ~48×/day only to decline.
- **Single-flight is a lease, not a lock.** `sync_checkpoints` gains `poll_claimed_at` with a TTL, claimed by one conditional upsert that fuses the staleness gate and the claim. A transaction-scoped guard cannot work here — the importer commits per batch and would release it mid-run. Concurrent in-process triggers coalesce first, so a page load costs one poll, not one per route.
- **Workflow feedback**: saves write the returned entity straight into the query cache (no stale window), run state reconciles wherever it is rendered, and workflow detail endpoints return real run counts.

→ [details](docs/backlog/v0.10.x.md#v0101-continuous-play-polling)

## [0.10.0] — 2026-07-17

**Play history you can finally trust — no matter what you import, or in what order.** Until now, merging Spotify and Last.fm plays depended on which import ran first: measured on real overlap data, one order merged 519 overlapping pairs correctly while the reverse double-counted 267 of them. v0.10.0 makes canonical plays a deterministic projection of an immutable observation ledger — re-import the same GDPR file twice, import Last.fm before or after Spotify, upload a fresh export over old data: the history comes out identical every time, and each play carries the richest field values any source offered (`ms_played` and behavioral detail from the export, second-precision start times from Last.fm, `loved` flags intact).

What you can do now:
- **Re-import fearlessly.** Every source record is an immutable observation; canonical plays are re-derived from the set, so re-runs and duplicate uploads are mechanical no-ops (`NULLS NOT DISTINCT` dedup constraints close the NULL-`ms_played` hole that would have duplicated every Last.fm scrobble on a full re-import).
- **Rebuild on demand.** `mixd plays rebuild [--dry-run]` re-derives the entire canonical history from the ledger — any past merge defect or future algorithm change is repaired by replay, never bespoke surgery. Also available from the assistant/MCP with two-phase confirmation, and `--dry-run`'s counts now simulate the would-be state so the preview matches what a real run does.
- **Same song, one play.** Cross-service observations of one listen converge on normalized start time (export end − `ms_played`) with calibrated tolerances; a Last.fm-created "striptease" and Spotify-created "Striptease" no longer become two canonical tracks (casing fix at the resolver) nor two plays (exact-normalized bridge).

How it works:
- **Observation channels, not services**: grouping keys on `(service, import_source)` via a frozen `ChannelSpec` registry (priority, time semantics, timestamp quality, tolerance) — a future channel (Spotify API polling, Apple Music) is one registry entry, zero new dedup code. Union-find grouping gates on per-component channel sets so skip-restarts stay distinct; survivorship is per-field, richest-wins (`src/domain/matching/play_projection.py`, the repo's first hypothesis property tests pin permutation/idempotence/batch-boundary invariance).
- **Membership is materialized**: a `play_sources` join table (migration `042`) makes idempotence a mechanical no-op diff and preserves provenance; resolution write-back (migration `041` partial index) keys on the ledger natural key so conflict-skipped re-import rows heal. One day-chunked `PlayProjectionService` serves both import Phase 3 and the rebuild; `play_dedup.py` and its order-dependent merge are deleted.
- **Pre-ship deep review** (10 finder angles, adversarial verification) hardened the pipeline before first deploy: a chunk-scoped claim registry closes an FK-phantom insert and the 1→N split arm (silent play loss), the normalization shift is clamped to the fetch margin (multi-hour `ms_played` rows were silently unprojected), adoption heals legacy end-time rows on re-import, Phase 3 regained its transaction bracket, migration `040`'s batch collapse no longer exits early on 3+-row duplicate groups, and the `play_sources` connector FK got its missing index.

→ [details](docs/backlog/v0.10.x.md#v0100-convergent-play-history)

## [0.9.5] — 2026-07-16

**Your production library, from any agent.** Until now an MCP client could only reach a mixd library by launching `mixd mcp serve` locally against a local database. v0.9.5 makes the exact same read + confirmable-write tools reachable over authenticated HTTPS at `https://mixd.me/mcp` — point Claude Code, Cursor, or Claude Desktop at your hosted account from any machine, authorize it once in the browser, and it acts as you, scoped to your rows, with every write still gated by an explicit confirmation. One tool registry, three transports (in-app chat, local stdio, remote HTTP); the local stdio server is untouched.

What you can do now:
- **Drive your hosted library from any MCP client, anywhere.** `mixd mcp install --remote --client cursor|claude-code|claude-desktop` wires the client to the production URL; first use opens a one-time browser consent on your existing mixd account.
- **Per-user, no shared key.** A client authenticates *as you* via an audience-bound OAuth token; every tool call is row-level-security-scoped exactly like the web UI. Only accounts on the deployment's allowlist can complete consent.

How it works (for operators):
- **The remote endpoint is an OAuth 2.1 resource server *and* a minimal authorization server in one process.** mixd validates its own audience-bound EdDSA JWTs locally (no per-call auth round-trip) and serves its public key at `/.well-known/jwks.json`. The spike confirmed Neon Auth cannot be the AS (no authorize endpoint, no dynamic registration, fixed token audience), so mixd hosts the minimal AS itself — assembled from the `mcp` SDK's server-auth framework — while Neon Auth still owns the login the consent step rides on (`src/interface/api/oauth/`, `src/interface/mcp/http.py`).
- **Streamable-HTTP transport mounted at `/mcp`** (stateless, identity per request from the validated token `sub`), with the DNS-rebinding guard pinned to the deployment's hosts (`src/interface/api/app.py`, migration `039`).
- **Client registration is CIMD-first with a DCR fallback** — Anthropic clients resolve a URL `client_id` to a fetched metadata document (SSRF-guarded, pinned to a validated public IP); Cursor uses dynamic registration. Port-agnostic loopback redirect matching (RFC 8252) supports native clients (`src/interface/api/oauth/cimd.py`).
- **The two-phase write confirmation store moved to Postgres** so a preview on one Fly machine is confirmable on another (migration `038`); the guarantee no longer depends on both calls hitting the same process.
- **Consent is a focused page in the web app** gated on your existing session; approval binds the authorization code server-side to your account. Refresh tokens rotate on every use, and replaying a rotated-out token revokes its whole family. Redirect schemes are restricted to https (or http on loopback) to close an XSS-via-redirect sink.
- Remote MCP is **off unless `MCP_OAUTH_SIGNING_KEY` is configured** — a deployment without it serves no `/mcp`, `/authorize`, or `/token` and is otherwise unaffected. Setup: [deployment.md](docs/deployment.md#optional--remote-mcp-v095); connection guide: [guides/mcp.md](docs/guides/mcp.md#remote-production-connection).

[Full details](docs/backlog/v0.9.x.md#v095-remote-mcp-server-your-production-library-from-any-agent).

## [0.9.4] — 2026-07-16

**The assistant you shipped, hardened.** A full-series review of v0.9.0–v0.9.3 turned up the failure modes that only bite in real use — a chat panel that hangs after a dropped connection or a Stop click, a confirmation card that says "Saved" when nothing saved, a long-running import launched from chat that showed no progress at all — plus two prompt-injection gaps where attacker-controlled library text reached the model with its data markers stripped. All are fixed, with the tool assistant now reclaiming context budget on the pages that don't need workflow tools.

Reliability (chat you can trust when things go wrong):
- **The chat panel can no longer get stuck streaming.** A network failure on send, a Stop-button click, or hitting the 50-message cap all now cleanly finalize the turn instead of leaving the input disabled forever. `isStreaming` is derived from the messages rather than tracked as a separate flag that could desync (`web/src/stores/chat-store.ts`, `api/chat-sse.ts`, `components/chat/ChatPanel.tsx`).
- **Write confirmations tell the truth.** Confirm/cancel now shows a pending state and commits to "confirmed"/"cancelled" only after the server responds — an expired action, rate-limit, or error rolls the card back to its buttons instead of falsely showing success (`ChatPanel.tsx`, `ConfirmationCard.tsx`).
- **Long operations launched from chat show progress again.** The synthetic `operation_started` frame (which arrives with no preceding tool-start) was being silently dropped by the store; `setToolResult` now upserts so the progress card renders (`chat-store.ts`).
- **Feedback thumbs report real success/failure** instead of optimistically showing "Thanks" on a failed POST (`cards/WorkflowPreviewCard.tsx`).

Security (injection defense-in-depth):
- **The research subagent's summary is re-wrapped as `<user_data>` before it re-enters the main conversation** — previously the subagent stripped the markers when quoting library text, laundering any injected instruction into the write-capable main model (`src/application/chat/subagent.py`).
- **Operation-failure messages are wrapped too** — `query_operations` run-detail returned connector-originated error text (attacker-influenceable) unmarked (`dispatchers/operations.py`).
- **Live key validation is rate-limited** (5/min) with a bounded 10s timeout, closing an unmetered oracle for validating stolen `sk-ant` keys (`routes/assistant.py`, `infrastructure/chat/anthropic_adapter.py`).

Correctness & robustness:
- **The chat request path no longer 500s on transient failures** — the workflow-context fetch catches broadly (a `TaskGroup`-wrapped error was bypassing the typed handlers), `ToolExecutionError` is mapped to a 422 on the confirm path, and confirmed-action executor failures are wrapped uniformly so an MCP client gets a retryable error instead of a burned token (`chat.py`, `middleware.py`, `registry.py`).
- **In-stream SSE errors no longer leak internals** — unmapped exceptions surface "An internal error occurred" (matching the HTTP path) with the traceback logged (`chat_sse.py`).
- **MCP results no longer show literal `<user_data>` tags** to clients, and the stdio server survives being spawned with a read-only working directory (absolute `data_dir`, no import-time mkdir) and an absolute `mixd` path in the generated install config (`mcp/server.py`, `config/settings.py`, `mcp/install.py`).
- **Workflow versions past 500 are revertable again** (a paging-oriented default cap was rejecting valid ordinals), and `manage_playlist` update/delete validate the id up front so a bad id can't sit in a pending confirmation (`dispatchers/workflows_write.py`, `playlists_write.py`).

Efficiency:
- **Dashboard stats stop re-running on every message** — a per-user 60s cache replaces the 5-query aggregate that fired on every chat turn, including confirmation clicks (`chat.py`); the in-memory rate limiter sweeps stale keys (`rate_limit.py`).

Follow-ups folded in (the v0.9.4 candidate pool):
- **Page-contextual tool routing, refined** — the two flagship workflow tools (`describe_node`, `generate_workflow_def`, the registry's largest schema) are demoted off the always-hot prefix and promoted only on the workflows page, reclaiming ~2 hot-tool slots on every other page. A second cache breakpoint on the per-page promoted segment keeps the invariant core cached across navigation while a section's tools cache per-turn. The hint map is now validated at import against a canonical page set and a derived ≤N-per-page ceiling. Approach chosen after a July-2026 best-practices pass confirmed rule-based routing beats an embedding router below ~50 tools. **One-time effect: the first request after deploy re-warms the tools prompt cache.**
- **User flows §6.5 rewritten** from the pre-implementation "v0.9.0 Sketch" into the shipped assistant surface (six sub-flows, real endpoints, edge cases) (`docs/web-ui/01-user-flows.md`).
- **Convention drift reconciled** — four rules amended to match shipped-and-correct practice (streaming-loop use-case shape, LLM-adapter port imports, assistant-key credential carve-out, metric-provider bridge) rather than contorting the code.

DRY/cleanup: extracted a shared `commit()` envelope replacing 13 hand-rolled try/except blocks across the write dispatchers, a `ChatCard` shell across the chat cards, a public `iso()` helper (6 copies removed), a `CHAT_ERROR_CODES` table shared by the HTTP and SSE error paths, and removed a redundant `currentWorkflowDraft` store field. No dead code or backward-compat shims remained to remove — the series was clean on both.

→ [details](docs/backlog/v0.9.x.md#v094-follow-ups--hardening)

## [0.9.3] — 2026-07-12

**Point your own agent at mixd.** The tool surface the in-app assistant drives is now an MCP server, so any MCP-aware client — Claude Desktop, Cursor, Claude Code — can read from and act on your library over a local stdio connection. "Find my starred tracks from last year that aren't on my Friday playlist and add them" runs from your desktop agent, against the same parity-complete registry, with the same never-silent write confirmation.

- **`mixd mcp serve` / `mixd mcp install`** — a low-level stdio MCP server built from the shared `TOOLS` registry (`src/interface/mcp/server.py`); `install` prints the client config snippet (Claude Desktop / Cursor / Claude Code), pinning `MIXD_USER_ID` for non-default users so an agent acts on the right tenant's library. Identity resolves exactly as every CLI command does (`MIXD_USER_ID` → `DEFAULT_USER_ID`) — no new credential surface.
- **26 tools exposed, one shared classifier** — read + synchronous-write tools are exposed; the 3 `agentic` tools (sandbox, subagent, tool search) stay chat-only (an MCP client brings its own loop) and the 5 long-running writes are gated (see below). `src/interface/mcp/exposure.py` is the single classifier both the server and the generated capability matrix consume, so the doc can't drift from what's actually exposed. Annotations (`readOnlyHint`/`destructiveHint`/…) are derived from each tool's `kind`.
- **In-band two-phase confirmation** — every write previews first: a call without `confirm=true` returns `{ status: "needs_confirmation", confirm_token, preview }`; a second call with the token and unchanged arguments commits. It reuses the chat `pending_action_store` + `execute_confirmed_action` (one preview renderer, two surfaces); expired/unknown tokens re-preview, args drift is rejected, and tokens are single-use with a 5-minute TTL.
- **stdout stays pure JSON-RPC** — structlog routes to stderr before the first import-time log for any `mixd mcp` command (early argv guard + a `console_stream` kwarg), so protocol output is never corrupted; `mcp install --print` likewise emits pure JSON for piping.
- **Long-running ops deferred, not forgotten** — imports, workflow runs, and playlist syncs need the MCP Tasks extension (and an `OperationLauncher` decoupled from FastAPI); they show `chat-only (pending Tasks)` in the matrix and flip to exposed when the post-2026-07-28 stable SDK lands. The SDK is pinned to `mcp==2.0.0b1` with a re-verify checkpoint baked into `pyproject.toml`.

→ [details](docs/backlog/v0.9.x.md#v093-mcp-server-mixd-as-a-tool-surface)

## [0.9.2] — 2026-07-12

**Ask for batch answers and deep analysis without watching the assistant grind.** With full parity the assistant could already do everything — but one visible tool call at a time, every intermediate result flowing through its context. v0.9.2 makes it efficient and long-horizon capable: "how many of my 2024 discoveries did I star, by month?" runs as one sandbox script over the read tools instead of hundreds of round trips, and "compare my listening this spring vs last spring and tell me what changed" is delegated to a research subagent that reads widely and hands back a single summary — the working thread stays fast and on-topic.

- **Code-execution sandbox + programmatic tool calling** — read tools are sandbox-callable (`allowed_callers` stamped in `build_tools`, reads only — mutations never), so the model writes Python that aggregates over the library and only the aggregate re-enters the conversation. A batch mutation composed in the sandbox still commits through one confirmable write proposal carrying the full change list. Gated by `CHAT__ENABLE_CODE_EXECUTION` (default on; disabling degrades cleanly to direct calls). A separate sandbox-round backstop (`max_turns × 5`) keeps a runaway code loop from starving the model-turn budget.
- **Research subagent (`delegate_analysis`)** — a fresh read-only loop at low effort with a bounded turn budget (12); its entire output is one dense summary string in the parent conversation, so a deep investigation costs the main context one tool result instead of a nested transcript. Read-only by construction (`build_subagent_tools()` is the read slice only — no writes, no sandbox, no nested delegation).
- **Tool search + deferred loading** — at 34 tools the registry flipped **deferred-first**: 7 tools load upfront (4 hot reads + 3 agentic), the rest are discovered on demand via a BM25 `tool_search` tool, with a system-prompt "search before you claim you can't" nudge. Discovered schemas append rather than swap, preserving the tools/system prompt cache.
- **Page-contextual tool routing** — the web client sends the coarse UI section you're on and its tools promote into the loaded set (rule-based, ≤3/page to hold the ~10-tool accuracy ceiling). Promoted tools ride the *uncached tail* so the cached core prefix is page-invariant — navigating between pages never re-pays for the tool schemas. Strictly additive: an unrouted page degrades to the static core + search. The restructure also fixed a latent bug where the cache breakpoint had been landing on a `{type,name}` server-tool block that rejects `cache_control`.
- **Context management + effort control** — the Quick/Standard/Thorough selector (low/high/xhigh, persisted locally, default Standard) rides every request; the subagent always runs at low effort regardless of the parent's selection.

→ [details](docs/backlog/v0.9.x.md#v092-agentic-depth)

## [0.9.1] — 2026-07-11

**Ask the assistant to do anything you can do in the app.** v0.9.0 could build workflows and little else; v0.9.1 makes the parity contract real — every read a user can perform in the web UI or CLI is a chat tool, every mutation is a two-phase-confirmed tool, long-running imports/syncs/runs stream their progress into the conversation, and whatever the assistant does lands in the same run log a human uses. "Which starred tracks haven't I played in six months? Tag them `context:revival` and add them to my Revival playlist" is now one conversation, end to end.

- **31 tools, parity closed** — the shared registry grew from 6 to 31 tools (14 read / 17 write). Every one of mixd's 82 application use cases is now either covered by a chat tool or explicitly excluded (blacklisted / mechanically-excluded / internal), enforced by CI: `NOT_YET_COVERED` is empty and the tripwire test asserts it stays that way. A generated `docs/web-ui/capability-matrix.md` (freshness-tested) is the audit.
- **Consolidated by intent, not use case** — tools are shaped around what a user wants ("manage tags", "query library"), 74 use cases collapsed to 31 tools via `operation`/`scope`/`view` discriminators, so the model reliably picks the right one.
- **Every mutation is a proposal** — write tools return a before/after preview; the confirmation card shows exactly what changes, with a distinct destructive-action banner (delete / merge / unlink) before anything commits.
- **Long-running operations in chat** — imports, playlist syncs, bulk assignment applies, and workflow runs launch from chat with the same live progress card the Sync page uses (reusing the existing operations SSE), and land in the run log attributed to the assistant (new `operation_runs.initiated_by`, migration `037`, Assistant badge in the import history).
- **Prompt-injection defense** — user-library free text (track titles, playlist/tag names) reaches the model wrapped in `<user_data>` tags and the frontend raw; a leaked title can't smuggle instructions.

→ [details](docs/backlog/v0.9.x.md#v091-full-capability-parity-in-app)

## [0.9.0.1] — 2026-07-11

**The AI assistant is now yours alone — bring your own Anthropic key, and until you do, it simply isn't there.** v0.9.0 mounted the chat surface for everyone behind one shared deployment key; that broke mixd's multi-user principle (one person's key and spend serving all) and showed a chat affordance that only errored when used. Now the assistant is per-user and opt-in: connect a key in Settings → Assistant (or `mixd assistant connect`), and the whole surface — Cmd+K, edge tab, side panel, mobile `/chat` and the "Ask" tab — appears only for you. No key, no assistant; no shared key, no broken button.

- **Per-user, encrypted, write-only credential** — the key is stored encrypted in the existing per-user token store (RLS-scoped, same discipline as connector tokens) and is never echoed back in any response.
- **Single resolution rule** — `resolve_chat_credential()` is the one place precedence lives (your key → optional single-tenant server fallback → none); `get_llm_client` and the `/assistant/status` capability signal both derive from it. A stored key that fails to decrypt fails closed rather than silently falling back to the shared server key.
- **Validated at connect, not first use** — a pasted key is checked with a minimal live completion, so a key with no billing is rejected up front instead of 500-ing on the first real message.
- **Frontend gated on a `useChatAvailable()` signal** — every assistant mount point consumes it; a desktop `/chat` deep-link waits for the gate to resolve before deciding, and cached Anthropic clients are closed on API shutdown.

→ [details](docs/backlog/v0.9.x.md#post-deploy-revisions)

## [0.9.0] — 2026-07-11

**Describe a playlist in plain English and get a real, editable workflow.** A persistent right-panel assistant (Cmd+K, edge tab when collapsed, full-screen `/chat` on mobile) turns "build me a chill weekend playlist" or "upbeat tracks I haven't played in 6 months" into a valid `WorkflowDef` — rendered in the same read-only graph the app uses everywhere else, refined in conversation, and saved to your workflow list on approval. This is the front door for anyone who'd never build a workflow from the node catalog by hand.

- **Natural-language workflow generation** — the `generate_workflow_def` tool's input schema is derived mechanically from the node catalog (`config_fields.py`), so the model proposes DAGs that reference real nodes with correct parameters; validation failures return as structured tool errors the model self-corrects from in the same turn. No few-shot templates.
- **In-graph preview + two-phase save** — a `WorkflowPreviewCard` embeds the existing `WorkflowGraph`; the assistant proposes `save_workflow` in the same turn, and nothing persists until you click Save (create) or Save changes (update an open workflow). Refinements replace the preview in place; superseded drafts collapse.
- **Enriched domain primer** — the system prompt carries the full node taxonomy, connector/preference/tag model, and per-user library statistics, split so the stable primer caches while volatile stats and current-workflow context trail it uncached.
- **Parity-classified tool registry** — every capability is a `ToolSpec` (read / write / agentic) that thinly adapts an application use case; a CI test asserts every one of the 81 use cases is classified, so "anything a human can do, the agent can do" can't silently rot. This registry is the foundation v0.9.1 fills, v0.9.2 deepens, and v0.9.3 exposes over MCP.
- **Feedback loop** — thumbs up/down (with an optional note) on any generated draft persist to a new `chat_feedback` table, so prompt and tool-description iteration is grounded in real accept/reject signal.
- **Under the hood** — 7-event SSE streaming, in-memory rate limiting (20/60s), two-phase confirmation store (5-min TTL, owner-checked), and untrusted-content tag-stripping, all ported from couplefins' production-tested v1.8.x agentic chat.

→ [details](docs/backlog/v0.9.x.md#v090-workflow-assistant-agentic-foundation)

## [0.8.18.3] — 2026-07-10

**CI stops leaking Neon database branches — the orphan pile-up that maxed the project's monthly limit in a week is fixed and purged.** Every direct push to main was creating a `ci/pr-<sha>` branch (the `github.event.number || github.sha` fallback) that no cleanup path ever deleted — `neon-cleanup.yml` only fires on PR close. 40 orphans had accumulated since April.

- **One-time purge** — all 40 orphaned `ci/pr-*` branches deleted; only `production` remains.
- **Explicit delete for push runs** — `ci.yml` now ends push-triggered runs with `delete-branch-action@v3` (`if: always()`, gated on the create step having succeeded), so the branch is removed even when tests fail.
- **TTL backstop** — every CI branch is created with `expires_at` (1 day for push, 7 days for PR) via `create-branch-action@v6`, so Neon auto-deletes anything orphaned by cancelled runs, runner crashes, or missed close events. PR-close deletion via `neon-cleanup.yml` is unchanged.

→ [details](docs/backlog/v0.8.18.md#post-deploy-revisions)

## [0.8.18.2] — 2026-07-09

**A dependency-freshness sweep — mostly internal currency, with one real client fix: filtering by multiple tags now serializes correctly.** Triaging the post-deploy Dependabot banner (42 alerts) showed ~41 were already remediated in the freshly-pushed lockfiles — the stale remote just hadn't re-scanned — and surfaced a handful of genuinely-behind direct deps worth taking while current.

- **Multi-tag filter fix** — regenerating the API client under orval 8.20 fixed array query-param serialization: `tag` filters now explode to `?tag=a&tag=b` (matching FastAPI's `list[str]`) instead of comma-joining, which the old client got wrong.
- **Backend** — uvicorn 0.49→0.51 (prod ASGI server), ruff 0.15.21, croniter, coverage, and transitive bumps via `uv lock --upgrade` (pydantic-core held to pydantic's pin). Full backend suite + basedpyright clean.
- **Frontend** — @xyflow/react, radix-ui, react-router, lucide-react, and the dev toolchain (biome, vite, vitest, knip, msw, shadcn) to latest-in-range.
- **TypeScript 6 → 7** — the native compiler generation, adopted with zero type errors across all 272 files.
- **Security floor** — `hono` floored to `>=4.12.25`. The residual `better-auth` Dependabot alerts stay open: forcing the patched line breaks the build because `@neondatabase/auth@0.4.2-beta` pins 1.4.18 and imports an export removed in 1.6.x. Those advisories are server-side (Neon-hosted), unreachable in our client usage; blocked on a Neon SDK update.

→ [details](docs/backlog/v0.8.18.md#post-deploy-revisions)

## [0.8.18.1] — 2026-07-04

**A follow-up review of Identity Integrity closed four latent defects before they could bite in prod.** No new user-facing surface — this is correctness hardening on the v0.8.18 matching layer: the Last.fm identity fold (migration 035) no longer aborts when a survivor's current identifier collides with another group's target; Last.fm's untrusted MBIDs can no longer merge two distinct recordings into one track; match reviews raised by non-`default` users route to their real owner instead of vanishing onto the shared default; and the identifier a track *displays* now always matches the mapping the system *promotes* to primary.

- **Migration 035 collision-safe rename** — a two-phase park-then-assign pass parks every to-be-renamed survivor at a temporary identifier before assigning finals, so a rename cycle can't trip the `connector_tracks` unique constraint and roll back the whole migration. The fold key is recomputed in Python (`strip().lower()`) to match the runtime mint, since SQL `btrim` leaves the Unicode whitespace (tab / newline / NBSP) the mint strips.
- **Last.fm MBID quarantine** — `track.getInfo`'s track-level MBID (untrusted per LB-431) no longer lands in the `musicbrainz` identity slot that feeds `save_track`'s `uq_tracks_user_mbid` merge key; it survives only in the enrichment log. The raw-spelling alias mapping is minted without auto-promotion so it can't demote the corrected import as the track's provenance.
- **Per-user review routing** — `create_review`'s upsert now keys on `user_id` too, matching the `uq_match_reviews_user_track_connector` constraint; non-`default` tenants' reviews were silently taking the RLS server-default and never surfacing to their owner.
- **One promotion policy** — display (`TrackMapper`) and promotion (`ensure_primary_for_connector`) share a single `(confidence desc, id asc)` total order; an integration test now fails if the two ever diverge on an equal-confidence tie.
- **DRY / internals** — a `compute_duration_diff_ms` domain helper replaces the ISRC duration-diff guard duplicated across 5 call sites; the drift panel reuses `list_pending_reviews`'s total instead of issuing a second identical `count_pending()` query.

→ [details](docs/backlog/v0.8.18.md#post-deploy-revisions)

## [0.8.18] — 2026-07-03

**Your library's identity data stops silently corrupting itself.** Identity Integrity hardens the matching layer in place: confidence scores still mean "how likely is this the right track" months after import, ISRC-reuse remasters route to review instead of overwriting your metadata, Last.fm plays/loves stop fragmenting across duplicate rows, and `mixd stats --matching` now surfaces resolution-health drift signals. **Confidence integrity (C1–C5):** re-imports record a `last_seen_at` freshness stamp (migration 034) instead of promoting 70-confidence guesses to a permanent 100; the re-resolution fast path returns stored provenance, not a synthetic 90; Last.fm's stale MBIDs are demoted to `artist_title` scoring; MISSING title/artist comparison levels + an ISRC-grade evaluation gate stop empty-metadata auto-accepts. **ISRC merge guards (epic 3):** `save_track`, Spotify inward dedup, and cross-discovery all reuse the engine's own `assess_isrc_match_reliability` suspect check — suspect collisions queue a review and mint a distinct ISRC-stripped canonical rather than clobbering; the Last.fm inward flow was rewritten reuse-before-create (cross-discovery now returns a `DiscoveryOutcome`), so one canonical holds both connectors' mappings and the dangling/orphan-canonical defect is gone. **Last.fm identity unification (epic 4):** all four identifier mint sites key on `make_lastfm_identifier` of Last.fm-corrected names (new `track.getCorrection` fallback; a raw-spelling alias mapping for fast re-import); migration 035 folds existing URL/MBID/case variants into the normalized scheme, RLS-bracketed and reattaching mappings, reviews, and playlist pointers. **Healing correctness (C6):** the denormalized `spotify_id` column syncs only for primary mappings, and one highest-confidence promotion policy replaces two contradictory ones. **Drift metrics (epic 6):** a confidence-band distribution + Drift Signals panel (fallback share, review inflow/depth/age, isrc_suspect pending, evidence divergence, stale denorm IDs) in `stats --matching` and `GET /stats/matching`, data-integrity check #7, and run-attached resolution counters in `operation_runs.counts`. A 10-test characterization net pinned every fix, so each landed as an assertion flip rather than a silent behavior change. Full suite green (3466 backend incl. both migration tests / 727 frontend).
→ [full stories](docs/backlog/v0.8.18.md)

## [0.8.17.2] — 2026-07-02

CI reliability fix — a green pipeline means green again. Fixed a Python-CI failure surfaced by the v0.8.17.1 push: `test_cadence_requires_at` asserts a literal substring of a Typer CLI error message; GitHub Actions' non-interactive runner truncates that rendered output via ambient terminal-width detection, reproducing deterministically on CI (2/2) but never locally (5+ attempts, including matched worker count and stripped credentials) — a rendering/assertion fragility, not a functional defect (the CLI validation itself is correct, exit code 2). Fix: `tests/unit/interface/cli/conftest.py` pins `COLUMNS=200`/`LINES=50` for the directory, removing the dependency on ambient detection.

## [0.8.17.1] — 2026-07-02

The running app now reports the version it actually is. The v0.8.17 tag was cut from a commit that predated its own version bump (a `git commit` invoked through a pipe masked a pre-commit hook failure, so the tag/release/deploy fired against `main` while `pyproject.toml` still read `0.8.16`). Purely a version-metadata + docs fix — the deployed *code* was already the complete, correct v0.8.17 closeout; this revision makes the running app's self-reported version, the OpenAPI schema, and the generated web client match the tag. Rides along: the identity-resolution research's roadmap sequencing (v0.8.18 Identity Integrity, v1.0.0 Mapping Supersession, v1.0.4 Data Sovereignty).

## [0.8.17] — 2026-07-02

**Sweep closeout: ratchet, review & dependency audit** — the quality gates the project depends on (visual regression, dead-code, ratchet baselines) are all live and honest again; closes the v0.8.12–v0.8.17 arc. The **ratchet re-census** (spoke 26) found no suppressed PLR rule at zero, so none flipped — the honest outcome, each re-documented dated in `pyproject.toml` (PLR1702's survivors sit in `apply_playlist_assignments` + the user-protected Apple Music tree; PLR0911's five are guard/classification chains where every return is a distinct log event or HTTP status; PLR0913/0917 remain the user's call, options in the spoke). The **noqa whodunit** traced the 14-vs-13 discrepancy to v0.8.7 (`d2d3a179`) and fixed it by code (`_emitter` rename, suppression deleted, count back to 13); vulture went from 12 findings to clean (8 dead `operation_summary` properties deleted — the 7 use-case copies never had a consumer, diff_engine's died with spoke 12; `retryable` whitelisted as frontend-read; `NO_ISRC`/`added_at_dates` whitelisted as dated parked decisions); ratchet baselines lowered (whitelist 79→63, pyright-ignore 20→18). The **visual gate revival** falsified its own premise: CI's e2e job had been red since 06-01 on image↔package skew (`v1.60.0-noble` vs Playwright 1.61.1 — browsers never launched); with the pin fixed, 16/18 baselines were still valid and only settings-sync ×2 regenerated (legitimate v0.8.4 schedule-card growth). Docker-pinned e2e + vulture + `check_ratchet.sh` joined CLAUDE.md's version-bump bar; two latent bugs fixed in the regen procedure (`CI=true`; pnpm 11.5 drops `--update-snapshots` after `--`). The **dependency audit** (findings recorded in `docs/backlog/dependency-audit-findings.md`, retired 2026-07-31 when its work orders closed) found the tree healthy — 0 unused runtime deps, versions current — with 10 work orders (headliners: undeclared direct `starlette` import, unwired `interrogate`/`bandit`, a hand-rolled retry in `base_repo.py`, the import-linter gap — v0.8.12's "already in place" claim was false). Closeout diff reviewed (engineer + QA agents): zero violations. Full matrix green (3386 backend / 727 frontend / 18/18 e2e in the pinned image). The **identity-resolution research pass** shipped alongside: design-space + governance memos, PDR-001/002, and the new `docs/decisions/` PDR system.
→ [full stories](docs/backlog/v0.8.13-0.8.17.md#v0817-sweep-closeout--ratchet--review)

## [0.8.16] — 2026-07-02

No user-facing changes — the sweep's two riskiest internal refactors landed safely, with the workflow engine's behavior pinned by new characterization tests first. **Sweep Wave 4: high-risk decompositions.** **Spoke 11** (Fable main thread) flattened the workflow executor's triple-nested closure stack — but characterization tests landed FIRST: 6 new tests pinning the observer event sequence (the SSE contract — previously unpinned; degrade emits `on_node_failed` but never `on_node_completed`), the timeout firing + node-identity rewrap path, and the first-ever real `run_workflow` executions. The flatten itself: a mutable `_RunState` (exactly the former closure captures; `parameters` by reference, `_shutdown_requested` deliberately left a live module global) with `_run_task_inner`/`_run_node_lifecycle`/`_build_and_execute_workflow` promoted to module level; AST nesting 4→2; move fidelity verified by AST-normalized diff (only intended deltas); all prior engine tests pass byte-unmodified. **Spoke 06** deleted the base-connector `get_playlist` reflection — exploration showed it was production-dead (Spotify overrides directly; no `get_<name>_playlist` methods exist), so migration became deletion — plus `get_connector_config` entirely (zero production callers, user-approved) and the mirrored conditional-`on_page` fork in the sync service; mock assertions retargeted stricter (`on_page=None` explicit). **Spoke 24 skipped** (user-approved): both work-order premises falsified — 72 import sites not ~30, and all 31 relationships are typed `Mapped[DBX]` annotations (not string targets), making the split a circular-import surface for navigation-only gain. Wave review: mechanical AST fidelity check + independent engineer review — zero findings. Full matrix green (3386 backend / frontend check+build; pre-existing noqa-baseline discrepancy unchanged, whodunit scheduled in the closeout).
→ [full stories](docs/backlog/v0.8.13-0.8.17.md#v0816-sweep-wave-4--high-risk-decompositions)

## [0.8.15] — 2026-07-02

Every screen's loading, error, and empty states now look and behave consistently. **Sweep Wave 3: frontend structure** — the frontend's whole structural-debt inventory in one deploy: three behavior-preserving spokes, one commit each, strictly sequenced 16 → 17 → 19. **Spoke 16** introduced the shared `QueryStates` wrapper (canonical loading → error → empty → success; discrete `loading` flag passed verbatim because `isLoading`/`isPending` diverge on disabled queries) + four skeleton primitives (`ListRowsSkeleton`/`BlocksSkeleton`/`CardGridSkeleton`/`DetailHeaderSkeleton`, bar dimensions as data for per-page pixel fidelity), migrating 9 screens off hand-rolled ladders, deleting 7 local skeletons, and normalizing Tags/ImportHistory error states onto `QueryErrorState` (new static `description` override keeps their copy verbatim). **Spoke 17** de-forked the cmdk track-search scaffold into `CommandSearchList` (owns search state, query, threshold, and the 3-state ladder) + `TrackResultRow`; `TrackSearchCombobox` deleted, `AddTracksDialog` rebuilt on the shell with consumer-owned checkbox/badge slots, `TagAutocomplete` deliberately skipped (shares only the outer scaffold). One approved visible copy change: "Searching..." → "Searching…". **Spoke 19** decomposed the oversized screens along the audited seams into 13 page-scoped component files — Dashboard 552→241, TrackDetail 554→306 (+`lib/match-methods.ts`), WorkflowRunDetail 511→210, PlaylistDetail 937→186, and Tags' three ConfirmationDialogs collapsed to one mode-keyed instance (copy byte-identical); Library + ConnectorPlaylistPickerDialog skipped (no clean seam). The aggregate wave diff was Fable-reviewed with every moved symbol mechanically diffed against its pre-wave original — zero findings. Full matrix green (3384 backend / 727 frontend + 96-state playlist-detail visual audit). Pre-existing visual-baseline staleness vs the freshly-installed Chrome 149 logged in the hub's Deferred list (failing set identical pre/post wave — not wave-caused).
→ [full stories](docs/backlog/v0.8.13-0.8.17.md#v0815-sweep-wave-3--frontend-structure)

## [0.8.14] — 2026-07-02

No user-facing changes — the backend's structural-debt inventory cleared in one deploy. **Sweep Wave 2: backend structure**, the bulk of the Fable Sweep: fourteen behavior-preserving spokes, one commit each, full matrix green at every step. The five use-case skeletons collapsed into `_shared` helpers (spoke 07); the six stray route handlers moved back behind `execute_use_case` (08); `update_canonical_playlist`'s diff sub-algorithms pushed down to domain (10); the play-importer pipeline + resolver decomposed with typed params and single-source username resolution (02/03); `ConnectorMappingSpec` replaced the mapping 7-tuple (21); the `list_tracks` filter builder + keyset pager extracted (22); mapper machinery split out of `base_repo.py` (23); plus the matching-loop hoist (04), Last.fm boundary validation (05), timed-query envelope (09), backend minor batch (15), service-method splits (14), and CLI structural pass (25). This ship also carries a **dependency refresh** landed ahead of the v0.8.15 frontend wave — react-router 7→8, cryptography 48→49, fastapi 0.139, Starlette 1.3, plus frontend/backend bumps (four <24h-old releases left to pnpm's release-age cooldown) — and schedules a **dependency audit** into the v0.8.17 closeout. Full matrix green (3322 backend / 699 frontend).
→ [full stories](docs/backlog/v0.8.13-0.8.17.md#v0814-sweep-wave-2--backend-structure)

## [0.8.13] — 2026-07-02

No user-facing changes, one latent UI bug fixed for free — dead code purged and the type floor raised. **Sweep Wave 1: dead code & free wins**, the first execution wave of the Fable Sweep (v0.8.12 audit). Five behavior-preserving commits, each with a `git grep` gate: the **type floor** locked first (8 basedpyright rules — `reportUnknown*` ×5, `reportMissingTypeArgument`, `reportImplicitOverride`, `reportDeprecated` — flipped `warning`→`error` after a fresh 0-warning verify), then four spokes. **Spoke 12** collapsed the dead execution-Strategy abstraction (Protocol + factory + `CanonicalExecutionStrategy` + `execute_with_strategy` + `ExecutionPlan`, ~75% of the file) to a single pure `plan_api_operations()`; the one live push path is unchanged (API-path integration tests green). **Spoke 13** purged `BatchConfig` + 14 pre-Fellegi-Sunter `MatchingConfig` fields across all four surfaces (domain config / factory / settings / `vulture_whitelist.py`); type-constraint tests were re-pointed to live `ConfidenceScore` fields, not deleted. **Spoke 18** deleted the dead `hasActiveFilters` export (Library already uses the tested `countActiveFilters`). **Spoke 20** added `status-success/error/warning/info` severity tokens and replaced raw Tailwind palette in three shared components — which also repaired a latent phantom `bg-status-error/10` (an undefined token Tailwind v4 was silently dropping) in `MergeTrackDialog`/`UnlinkMappingDialog` with zero edits to them. Full matrix green (3350 backend / 699 frontend).
→ [full stories](docs/backlog/v0.8.13-0.8.17.md#v0813-sweep-wave-1--dead-code--free-wins)

## [0.8.12] — 2026-07-01

**The Fable Sweep — audit & work orders** (docs-only, no deploy artifact): a full structural audit of the codebase producing a working hub + 26 self-contained refactor work orders, executed as v0.8.13–v0.8.17.
→ [details](docs/backlog/v0.8.12.md#v0812-the-fable-sweep--structural-hardening)

## [0.8.11] — 2026-06-30

**Manual playlist track editing**, the closing slice of the v0.8.x cycle: add, remove, and reorder a canonical playlist's tracks directly from its detail page, instead of round-tripping through connector sync or a workflow. Three frozen-Command use cases (`AddPlaylistTracks` / `RemovePlaylistEntries` / `ReorderPlaylistEntries`) + REST endpoints + CLI parity (`mixd playlist add-tracks` / `remove-tracks` / `reorder`) sit on the entry-identity threading already shipped in v0.8.7 (`PlaylistEntry.id`, `eq=False`), so the only load-bearing schema change was finally *exposing* `PlaylistEntrySchema.id`. The web surface is a **dnd-kit** sortable `PlaylistTrackEditor` (keyboard reorder + screen-reader announcements) + an `AddTracksDialog` multi-select search modal; **remove is optimistic with a deferred-commit "Undo" snackbar** — the `DELETE` waits out the snackbar window, so an undone removal never reaches the server and keeps the entry's `added_at`/position (re-truing flow 3.6 away from its old confirm-dialog spec). Manual add deliberately allows duplicates without disturbing the workflow-append dedupe path.
→ [details](docs/backlog/v0.8.11.md#v0811-manual-playlist-track-editing)

## [0.8.10] — 2026-06-29

Editor polish + trustworthy play-history config in one ship (it **absorbed the play-history-config work originally scoped as a separate feature** — the v0.8.9-review follow-ons). Four stories: (1) a typed **entry-intent state machine** (`load`/`seed`/`blank`, in `editor-entry.ts` + `useEditorEntry`) replaces the implicit `{ imported: true }` reset chokepoint so the navigation-surviving editor store is predictable on every entry — a fresh "New Workflow" never shows a stale draft (and a latent web-suite "1 error" was traced + fixed along the way: a partial recovery snapshot crashing `useWorkflowSSE`); (2) **browse-to-link** retires paste-an-ID on Playlist Detail by reusing the existing import picker in a new single-select `mode` — no new backend (the browse method + endpoint shipped in v0.8.7–v0.8.8); (3) **config-aware validation** — `filter.by_metric`/`sorter.by_metric` check what the upstream `play_history` enricher is *configured* to emit, not its capability, so the editor's green check stops certifying empty-result workflows; plus a `period_days`-is-inert warning (which revealed + cleaned an inert `period_days` in all 9 play-history templates); (4) a **breaking day-window rename** — `min/max_days_back` → `not_played_in_days`/`played_within_days` — across code + seed JSON + a first-of-its-kind JSONB key-rewrite migration (033) over the three `WorkflowDef` columns. **Sub-flows/node-grouping was cut** as the wrong solution to a real problem (40+ node navigation) — recorded in [unscheduled.md](docs/backlog/unscheduled.md) for a lighter approach.
→ [details](docs/backlog/v0.8.9-0.8.10.md#v0810-editor-polish--trustworthy-play-history-config)

## [0.8.9] — 2026-06-28

**Workflow templates & import/export** — curated template gallery content plus workflow import/export, on the clone-on-use plumbing that shipped early as 0.7.8.20.
→ [details](docs/backlog/v0.8.9-0.8.10.md#v089-workflow-templates--importexport)

## [0.8.7 – 0.8.8] — 2026-06-25

**Import/sync reconciliation**, the correctness epic that fixed a *very broken* Spotify import + sync. One `PlaylistReconciliationEngine` now fetches the real remote fresh and diffs at the connector-identifier level — killing the self-join that silently dropped tracks, made push a no-op, and left the destructive guard dead. Unmatched tracks survive as first-class **unresolved** rows, destructive syncs are gated behind a real `confirm_token` 409 round-trip, and every run leaves a durable `OperationRun` audit row. **v0.8.7** shipped the engine + REST + CLI (`import-spotify` / `sync` / `sync-preview` / `repair`, per-item-atomic batches, position-aware duplicate removal on push). **v0.8.8** brought it to the web: a headless `OperationsProvider` that surfaces overnight failures + an "N running" sidebar badge, **Retry failed only** (server-reconstructed from the audit row via a pure `OperationRun.is_retryable`), the destructive-sync confirm dialog, additive imports with honest per-playlist progress + unresolved bulk-repair, and one `DirectionChooser` direction vocabulary. A web-robustness pass rode along in the same ship — SSE stream-end REST reconcile, toast-ledger dedup, render-pure recovery gate, and auth-gated/non-background polling extracted to a shared `useAdaptivePollingList`.
→ [details](docs/backlog/v0.8.7-0.8.8.md#v087-importsync-reliability-backend--cli)

## [0.8.6] — 2026-06-18

Sync results are now reported honestly — a partial push errors instead of silently claiming success, and tracks with no match on the destination are surfaced instead of vanishing. **Cycle hardening & cleanup** from the 2026-06 design-debt review: split the 2,062-line `repositories/interfaces.py` into 15 per-aggregate protocol modules; extended the `lazy="raise_on_sql"` eager-load guard across the whole ORM graph (eager-coverage audit + `passive_deletes` belt-and-braces, every relationship now carries an explicit `lazy=`); collapsed the `update_connector_playlist` verification ceremony and `base_repo` speculative periphery. Connector push now reports honestly via `PlaylistOpsOutcome.fully_applied` — a partial push routes to ERROR instead of a silent SYNCED, and canonical tracks with **no match on the destination** surface as an "unmatched" count in the CLI sync output and a **gold tooltip chip** on the web Playlist Detail (persisted via `last_sync_tracks_unmatched`, migration `029`). A code-review pass also caught a suppressed-error ADD false-success and an orphaned `@db_operation("delete")` decorator. Two rule carve-outs reconciled (irreducible SQLAlchemy-reflection suppressions; the interface→infra OAuth-access exception).
→ [details](docs/backlog/v0.8.5-0.8.6.md#v086-cycle-hardening--cleanup)

## [0.8.5] — 2026-06-16

**Operation & surface reliability** (design-debt review): SSE-seam lifecycle inversion + a data-loss fix for stranded connector plays (with a dry-run backfill script), server-safe OAuth tokens, a Last.fm cross-tenant fix, and 409 pre-flight guards.
→ [details](docs/backlog/v0.8.5-0.8.6.md#v085-operation--surface-reliability)

## [0.8.4] — 2026-06-09

Background **sync scheduling** + proactive **failure surfaces**, closing the "sync overnight → rebuild in the morning" loop. Daily/weekly schedules for the three sync targets (`lastfm:plays`, `spotify:likes`, `lastfm:likes`) live on their existing Settings›Sync cards via a shared `ScheduleCard` + `useScheduleController` (the bespoke `WorkflowScheduleCard` was refactored onto the same pair — zero new backend; the v0.8.2/8.3 engine already covered it). Two discovery-without-checking surfaces ride the already-fetched `GET /schedules`: a dashboard aggregate `ScheduleFailuresBanner` and an amber "Failing" marker on each workflow row, both self-clearing on the scheduler's success reset (one shared `AlertBanner` primitive behind both the per-schedule and aggregate banners). Also folded in: a human-facing per-workflow `run_number` (migration `027`, shown instead of the UUID), the `loaded_list`/`loaded_one` no-I/O mapper read primitives + a scoped 7-relationship `lazy="raise_on_sql"` guard (down-payment on the v0.8.6 eager-load-hardening epic), and a toolchain/dependency bump pass (SQLAlchemy 2.0.50, uv 0.11, node 24, pnpm 11.5.2, flyctl 1.6, Playwright 1.60).
→ [details](docs/backlog/v0.8.0-0.8.4.md#v084-background-sync-scheduling)

## [0.8.3] — 2026-06-07

Workflow scheduling **web UI** + an unplanned **workflow-page redesign with live-run reconnection**. A timezone-aware `SchedulePicker` (daily/weekly toggle, no cron) sets automation; the workflow list shows a "Next run" column sourced from a single caller-scoped `/schedules` fetch (no N+1). The detail page replaced the loose pipeline strip + `LastRunCard` with one state-aware `WorkflowStatusPanel` (active/idle/never-run) + a dedicated `RunHistoryTable`, and now reconnects to an in-flight run after reload via an app-global active-runs source + DB snapshot adoption. A paired scheduler fix persists an `operation_id` on scheduled runs so they're reconnectable, and the review pass unified schedule advancement into one fresh-read `_release` transaction. **Partial:** only the per-schedule failure badge shipped; the proactive dashboard banner + workflow-list failure indicator were carried to v0.8.4.
→ [details](docs/backlog/v0.8.0-0.8.4.md#v083-workflow-scheduling---web-ui--failure-alerts)

## [0.8.2] — 2026-06-07

Workflow scheduling **engine & CLI** — playlists can now rebuild themselves on a schedule. DB-stored daily/weekly schedules (no freeform cron; `croniter` kept only as the internal DST-correct next-occurrence engine over `zoneinfo`) fire from an in-process poll loop in the FastAPI lifespan, built on a shared `run_periodic_background_loop` (the sweeper was retrofitted onto it). Resilience: optimistic per-tick claim, a txn-level advisory poll-lock for multi-instance leader election, a stuck-start reaper-as-skip (no failure-streak bump), per-user OAuth-token isolation, and a single `schedules` table with an exclusive-arc CHECK for workflow-vs-sync targets. Drivable end-to-end via `mixd workflow schedule` / `mixd sync schedule`.
→ [details](docs/backlog/v0.8.0-0.8.4.md#v082-workflow-scheduling---engine--cli)

## [0.8.1] — 2026-05-31

No user-facing changes — the app boots leaner and runs workflows on its own engine. Workflow engine swap: Prefect 3 removed in favor of a homespun stdlib-asyncio DAG executor. Parallel execution levels are computed via Kahn's topological sort (a pure domain function) and each level runs in an `asyncio.TaskGroup`; run-state, cancellation (SIGTERM), and fault tolerance are owned in-process. Prefect and its full transitive tail are gone — the app no longer imports an embedded orchestration server at boot. The `workflows/` package was reorganized into `definition/`, `engine/`, and `nodes/`. Review pass also fixed primary-input track-count diagnostics and made lifecycle-observer emission best-effort.
→ [details](docs/backlog/v0.8.0-0.8.4.md#v081-workflow-engine-swap-prefect-to-stdlib-asyncio)

## [0.8.0] — 2026-05-30

Workflow runs fail loudly and accurately instead of silently or wrongly. **Run reliability & validation hardening** opened the v0.8.x scheduling cycle: a first-writer-wins terminal-write guard, a distinct `crashed` status (worker died) vs `failed` (logic broke), an OS-thread heartbeat watchdog that survives a blocked event loop, three closed silent-wrong-result validation gaps, and a SIGTERM-shielded connector cleanup.
→ [details](docs/backlog/v0.8.0-0.8.4.md#v080-run-reliability--validation-hardening)

## [0.7.8.20] — 2026-05-30

Workflow "kinds" consolidated — the read-only built-in **template** kind was eliminated in favor of a file-backed template **gallery** + clone-on-use, leaving a single editable `Workflow` entity (migration `023` drops `is_template`/`source_template`, the read-only guards, and shared `user_id IS NULL` rows). Clone-on-use mints a fresh unique slug, and Duplicate runs through a single-transaction `DuplicateWorkflowUseCase`. This delivered most of v0.8.9's template *plumbing* early; v0.8.9 then scoped down to curating the template content + import/export.

## Earlier releases (v0.2.7 – v0.7.8)

One line per ship; full stories live in the [archived version files](docs/backlog/completed/).

- **v0.7.8** (2026-05-09) — Mobile responsiveness + visual regression baseline (Playwright `toHaveScreenshot`) · [details](docs/backlog/completed/v0.7.8.md)
- **v0.7.7** (2026-04-26) — Operation Run Log: persisted import history + post-run toast · [details](docs/backlog/completed/v0.7.7.md)
- **v0.7.6** (2026-04-22) — Tag maintenance & single-playlist Spotify polish · [details](docs/backlog/completed/v0.7.6.md)
- **v0.7.5** — Workflow integration & quick filters · [details](docs/backlog/completed/v0.7.4-5.md)
- **v0.7.4** — Tag & preference bootstrap: bulk-map playlists to tags/preferences · [details](docs/backlog/completed/v0.7.4-5.md)
- **v0.7.3** — Playlist browser: browse & import Spotify playlists · [details](docs/backlog/completed/v0.7.2-3.md)
- **v0.7.2** — Tagging system: categorize tracks by mood, energy, context · [details](docs/backlog/completed/v0.7.2-3.md)
- **v0.7.1** — Preference sync from likes with original dates · [details](docs/backlog/completed/v0.7.0-1.md)
- **v0.7.0** — Preference system: rate tracks as hmm/nah/yah/star · [details](docs/backlog/completed/v0.7.0-1.md)
- **v0.6.0 – v0.6.12** — Multi-user: schema, per-user OAuth, data isolation, first-class CLI, SSE resilience, Neon platform integration, zero-Any codebase · [details](docs/backlog/completed/v0.6.x.md)
- **v0.5.0 – v0.5.10** — Infrastructure: CI/CD, PostgreSQL migration, containerized Fly.io deploys, OAuth + WCAG AA + theming, parallel execution, security hardening, the narada→mixd rename · [details](docs/backlog/completed/v0.5.x.md)
- **v0.4.0 – v0.4.11** — Workflows era: persistence, execution, visual editor, connector linking, provenance & merge, data-integrity audit, CLI unification · [details](docs/backlog/completed/v0.4.x.md)
- **v0.3.0 – v0.3.3** — Web UI foundation: playlists, imports with real-time progress, track library, dashboard · [details](docs/backlog/completed/v0.3.x.md)
- **v0.2.7 and earlier** — CLI era: like sync, play history import, Clean Architecture rebuild, playlist diff engine, workflow transforms · [details](docs/backlog/completed/v0.2.x.md)

---

## Historical (pre-v0.5, git-cliff era)

The sections below are the original auto-generated changelog, preserved as-is. It stopped being
maintained around v0.4.11; the "Unreleased" heading it accumulated under is retitled here. Some
documents it references (`ROADMAP.md`, `ARCHITECTURE.md`, `REFACTORING_PROGRESS.md`) have since
been retired into `docs/`.

### v0.4.x-era tail (formerly "Unreleased")

#### Bug Fixes

- post-migration cleanup from /simplify review
- checkpoint always-write-on-exit, Unset sentinel for cursor, commit-before-SSE
- async cleanup task leak and optimize test suite speed
- remove environment variable pollution and test fixture anti-patterns
- resolve LastFM e2e test failures and improve code quality
- resolve enricher key types and track repository test failures
- resolve 7 of 11 test failures through infrastructure fixes
- resolve type checking issues and format code

#### Documentation

- update ARCHITECTURE.md and ROADMAP.md for current codebase
- Phase 2 complete - SKIPPED conversion utilities (differences are semantic)
- comprehensive modernization progress with context and Phase 11 test review
- update REFACTORING_PROGRESS.md for Phase 2 completion
- complete Clean Architecture migration documentation

#### Features

- upgrade Vite 7→8 (Rolldown), adopt codeSplitting + tsconfigPaths
- workflow CLI CRUD + agent modernization (v0.4.11)
- CLI workflow runs create DB records + frontend design token consistency
- v0.4.10 — cross-source play history deduplication
- v0.4.9 — data integrity, identity resolution, test suite & type audit
- v0.4.8 — usability & self-explanatory interface pass
- connector track mapping management — relink, unlink, set primary
- v0.4.6 — track provenance & merge
- v0.4.4 — connector playlist linking + documentation restructure
- workflow fault tolerance, track invariants, and execution diagnostics
- add dynamic metric columns to workflow output tracks
- v0.4.3 — visual workflow editor, preview, versioning & diff
- v0.4.2 — run output persistence, pipeline strip, run-focused UI
- v0.4.1 — workflow execution, run history, live DAG status
- v0.4.0 — workflow CRUD, enricher nodes, workflow web UI, Spotify library/contains
- stale Spotify ID resolution with redirect detection and search fallback
- pre-flight connector validation before workflow execution
- dry-run mode + per-node execution records
- execution guard preventing concurrent workflow runs
- task timeouts + on_failure hook for workflow nodes
- NodeExecutionObserver protocol + refactor progress out of execute_node
- sub-operation progress tracking, workflow extraction, and simplify cleanup
- v0.3.3 — dashboard stats, version flow fix, roadmap resequencing
- track library pages, settings redesign, and nested API config
- v0.3.2 — DRY refactoring, checkpoint batching, and docs restructuring
- v0.3.1 — imports & real-time progress in the web UI
- v0.3.0 — web UI foundation, FastAPI backend, and playlist CRUD
- add multi-artist fallback and improve Last.FM logging levels
- improve metric display and update dependencies
- preserve playlist track metadata with PlaylistEntry pattern
- consolidate progress system and enhance Spotify operations
- implement unified progress tracking with Progress.console coordination
- resolve LastFM concurrency bottleneck and DRY code consolidation
- implement modular play import system with connector-specific factories
- implement comprehensive batch processing and error handling infrastructure
- complete Last.fm import system with enhanced track resolution and pagination
- implement unified Spotify import service with enhanced track identity resolution
- complete unambiguous identity pipeline refactor with clean architecture
- add data retrieval use cases and enhance play history management
- implement ultra-DRY enhanced playlist naming with template support
- complete infrastructure and workflow system overhaul
- implement comprehensive play history filtering and database optimization
- complete v0.2.4 playlist workflow expansion with comprehensive refactor
- complete Clean Architecture migration with UnitOfWork pattern and perfect code quality
- fix runtime workflow execution failures and implement comprehensive test improvements
- resolve SQLite database locking issues with session management improvements
- implement comprehensive metadata management and CLI restructuring

#### Maintenance

- v0.4.10 cleanup — resolver consolidation, branding, rules refinement
- migrate from Poetry to uv
- bump dev-setup-guide submodule to 61fe1b1
- move completed/ into backlog/, audit dev-setup-guide for clarity
- v0.4.5 — code & test suite hardening
- add claude rules and running log
- reorganize docs and add claude agents
- upgrade to Python 3.14.2 and update all dependencies
- remove 2 unused imports

#### Performance

- optimize bulk_upsert and update with identity map pattern

#### Refactoring

- codebase tighten pass — purge dead code (~7k lines: one-shot scripts, SQLite-era migration archive, test-only methods, dead endpoints/fields/enums), shrink suppression debt, consolidate checkpoint access, extract webhook verification + workflow background execution, fix stale docs/skills
- split Settings into Integrations + Sync sub-pages with collapsible sidebar nav
- DRY cleanup across repositories and frontend
- simplify v0.4.6 per code review
- extract dev-setup-guide into git submodule
- remove dead code, harden static analysis, and enforce strict track invariants
- dark editorial UI polish, extract SectionHeader, fix decodeHtmlEntities
- atomic sync+upsert in playlist_source connector branch
- restructure modules, remove dead code, and align test paths
- typed connector protocols, per-UoW caching, and codebase cleanup
- relocate modules to correct layers and redesign metrics system
- type architecture cleanup and Pydantic boundary validation
- migrate to native async httpx, fix logging, harden source nodes
- DRY consolidation and web interface readiness
- migrate from backoff to tenacity for retry policies
- configure tooling for Python 3.14 and fix string literal bugs
- modernize to Python 3.14 patterns in src/
- add slots=True to result value objects for memory efficiency
- convert remaining @dataclass to @define for 100% attrs consistency
- replace validate() methods with attrs field validators (Phase 1)
- extract BaseMatchingProvider to eliminate workflow duplication
- extract HTTPErrorClassifier base class to eliminate duplication
- eliminate duplication and modernize use case layer
- modularize domain transforms and eliminate duplication
- complete connector architecture migration and code reorganization
- restructure connector architecture with modular design
- enhance operation tracking and progress monitoring across import services
- modernize track matching system with pluggable provider pattern
- restructure project to clean architecture with src/ layout

#### Testing

- add comprehensive test coverage for new matching system

#### Cleanup

- remove redundant session regression test and complete Clean Architecture migration
