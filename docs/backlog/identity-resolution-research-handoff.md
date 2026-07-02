# Handoff: Identity Resolution Deep Dive — Research Pass

**For**: a research agent starting fresh on this codebase.
**Mission**: understand how mixd maps a canonical track to its representations on Spotify, Last.fm, and MusicBrainz; research 2026 best practices for entity/identity resolution in the music domain; assess what folding Apple Music into this process requires; and produce a **design-space memo** (options with trade-offs, not a single answer) aimed at making identity resolution more robust, more fault-tolerant, and — where feasible — less fragile.

**This is a research pass.** The deliverable is a memo plus backlog stories, not code. Do not refactor anything. Where the current design looks odd, treat it as a question to investigate (it may encode a hard-won lesson — check git history before judging).

**Scope extension (2026-07-02)**: while the matching stack is in context, also map the design space for **artist-level identity** — v0.10.0's Artist Identity Resolution epic (Spotify artist ID ↔ Last.fm artist name ↔ MusicBrainz artist MBID, and the alias/abbreviation problem: "TEED" vs "Totally Enormous Extinct Dinosaurs", where text normalization can't bridge and the current sketch is a MusicBrainz-alias lookup table). One section in the memo, not a second memo.

**Scope extension (2026-07-02, omni-integration)**: Phase C gains a survey-depth **breadth ring** (Tidal, Deezer, SoundCloud, YouTube Music) with a curation-capability lens, and Phase D's memo gains a **connector-contract scalability assessment** — what an 8–10-service future demands of the connector abstractions, judged against the full provider capability matrix. Details live in those phases; the durable/perishable rule there governs what gets researched now vs. marked "re-verify at implementation."

**Why this assignment exists**: a mid-2026 DRY/DDD tighten pass swept the whole backend and barely touched identity resolution. Do not read that as a clean bill of health. That pass audited *hygiene debt* — suppressed warnings, whitelisted symbols, dead endpoints, lint-visible duplication — and the matching engine passes every static gate. What it never did is interrogate the *design*: whether the confidence model matches reality, whether identity assertions outside the engine are sound, whether the abstractions sit at the right altitude, whether failure handling covers the failure modes that actually occur. Hygiene-clean and design-sound are different properties; your job is the second one. The one place the pass did generate identity-adjacent findings, it deliberately deferred them (§2b below).

---

## 1. What "identity" means here today (orientation map)

A **canonical Track** (user-owned, RLS-scoped) carries direct identity columns (`isrc`, `spotify_id`, `mbid`) plus a set of **mappings** to **connector tracks** (cached external representations, globally shared). A mapping carries `match_method` (direct / isrc / mbid / artist_title / existing_mapping / …), `confidence` (0–100), structured `confidence_evidence`, `origin` (automatic vs manual), and an `is_primary` flag (unique partial index per user+track+connector).

| Concern | Where | Role |
|---|---|---|
| Identity data model | `src/domain/entities/track.py`, `track_mapping.py`; `src/infrastructure/persistence/database/db_models.py` (DBTrack, DBConnectorTrack, DBTrackMapping, DBMatchReview) | Canonical track ↔ connector track ↔ confidence-scored mapping; review staging table |
| Matching engine (pure domain) | `src/domain/matching/` — `algorithms.py`, `config.py`, `evaluation_service.py`, `probabilistic.py`, `text_normalization.py`, `isrc_validation.py`, `types.py` | Five-tier title similarity (exact→variation→diacritics→phonetic→fuzzy), Bayesian weighting, duration penalties, ISRC-suspect detection (>10s duration delta flags remaster reuse), three-zone evaluation (auto-accept / review / reject) |
| Orchestration | `src/application/use_cases/match_and_identify_tracks.py` | The sole resolution orchestrator: existing-mapping fast path → provider raw matches → domain evaluation → persist |
| Inward resolution (external ID → canonical) | `src/infrastructure/connectors/_shared/inward_track_resolver.py` (base 3-step: mapping lookup → canonical reuse by title/artist → create); `spotify/inward_resolver.py`; `lastfm/inward_resolver.py` | Spotify has three scenarios: DIRECT (100), REDIRECT (Spotify relinking, 100, dual mapping), SEARCH_FALLBACK (dead ID → artist+title search, 70, dual mapping). Last.fm uses `artist::title` identifiers + track.getInfo enrichment, sequential |
| Outward matching (canonical → provider) | `_shared/matching_provider.py` (template: partition by ISRC availability → `_match_by_isrc` → `_match_by_artist_title` fallback); per-connector implementations (spotify, lastfm, musicbrainz) | Providers return raw matches only; zero business logic in infrastructure |
| Cross-service discovery | `spotify/cross_discovery.py` (+ `domain/matching/protocols.py` CrossDiscoveryProvider) | Last.fm resolver can discover Spotify mappings; optional ListenBrainz Labs pre-resolution; ISRC-collision check maps to an existing canonical instead of duplicating |
| Healing | `repositories/track/mapper.py` (`_promote_fallback_to_primary` on read), `set_primary_mapping` in `repositories/track/connector.py` | Read-path auto-promotion when a connector lacks a primary mapping |
| Human-in-the-loop | `resolve_match_review.py` use case, `repositories/match_review.py`, review API routes (kept for the v1.0.x review UI) | Medium-confidence matches staged in DBMatchReview; accept creates origin=MANUAL_OVERRIDE mapping |
| Normalization | `connectors/_shared/isrc.py` (`normalize_isrc`, ~12 call sites); `domain/matching/text_normalization.py` | ISRC: strip hyphens/uppercase/validate. Text: diacritics, feat./ft. equivalences, parenthetical stripping, Metaphone |
| Diagnostics | `scripts/diagnose_stale_spotify_ids.py`; `tests/integration/connectors/spotify/test_stale_id_fallback.py` (diagnostic-marked, needs real token) | Measures Spotify ID churn (alive / redirected / dead) against real exports |
| Apple Music today | `src/infrastructure/connectors/apple_music/` — `connector.py` (factory returns a bare stub, `auth_method="coming_soon"`), `error_classifier.py` skeleton | Registered for UI "coming soon" only; zero identity capability; excluded from coverage; v1.0.1 roadmap item |
| Prior art in-repo | `docs/backlog/v1.0.x.md` — v1.0.3 "Cross-User Identity" | Sketches global `ConnectorIdentity` + `ObservationLedger` with trust weighting, scheduled MusicBrainz re-validation. **Existing thinking to evaluate, not a decided design.** |

Config knobs live in `MatchingConfig` (`domain/matching/config.py`): per-method base confidences, **two threshold systems** (three-zone auto-accept/review thresholds AND legacy per-method floor thresholds — both present), duration tolerance/penalties, similarity tiers.

---

## 2. Known tensions and fragility signals (investigate, don't presume)

These came out of a recent codebase audit. Each is a question, not a verdict:

1. **Per-user mappings vs global connector tracks.** `connector_tracks` are global; `track_mappings` are RLS-scoped observations. The v1.0.3 backlog calls this out for sharing/gaming/drift. How much of today's fragility traces to this split?
2. **Two threshold systems coexist** in MatchingConfig (three-zone + legacy per-method floors, the latter labeled "legacy" in settings comments). Which is load-bearing where? Is the legacy system dead, alive, or half-alive?
3. **ISRC is treated as near-truth but is known-unreliable** (remaster/clean-explicit reuse — the code itself documents this and applies a duration-based suspect check). Where does ISRC-as-primary-signal leak through *without* the suspect check (e.g., `Track.has_same_identity_as`, cross-discovery collision logic, repo-level lookups)?
4. **`normalize_isrc` is called at ~12 boundary-ish sites** rather than once at a validated boundary. A prior pass floated an `Isrc` value object (validate-once-at-the-Pydantic-boundary) and deliberately deferred it. Would boundary validation actually remove failure modes, or just move code?
5. **Identity healing is read-path-triggered** (auto-promotion happens when a track is *loaded* with a session). What never heals because it's never read? Is there a case for scheduled re-validation (the v1.0.3 sketch says yes via MusicBrainz; verify the premise)?
6. **Last.fm has no stable track ID** — `artist::title` strings and URLs are the identifiers. How does that interact with renames/remasters, and what does it imply for any "stable external ID" assumption in a future design?
7. **Spotify ID churn is real and measured** (the diagnostic script exists because exports contained dead/redirected IDs). What's the actual churn rate from the last diagnostic run, and is the dual-mapping (new=primary, dead=secondary) cache strategy aging well?
8. **Match failures surface only via the review queue and logs** — no metrics/alerting on resolution quality drift. What would "we'd notice if matching silently degraded" look like?
9. **Sequential per-track API calls** in the Last.fm path (no batch getInfo). Fragility under rate limits / partial failure?
10. **`EXISTING_MAPPING_CONFIDENCE = 90`** — re-encountering a mapped track asserts confidence 90 regardless of the original match method's confidence. Intentional? Consequences?

---

## 2b. Unexecuted findings from the tighten pass (verified, deferred — your warm leads)

The tighten pass's duplication audit confirmed these and consciously left them on the table (they're behavior-adjacent and sit in your territory; consolidating them blind, without the design understanding you're about to build, risked enshrining the wrong abstraction). Treat them as **probes**: each is a thread that may unravel a bigger design question.

| Lead | Where | Finding | The deeper question it points at |
|---|---|---|---|
| DUP-01 | `lastfm/client.py:222` (+301, +367) | Last.fm error-envelope check duplicated 3× verbatim | Error-contract handling is per-connector and ad-hoc — what's the connector-agnostic shape? |
| DUP-02 | `lastfm/operations.py:77` | ~37-line classified-error logging block duplicated across two getInfo paths, callers silently return `.empty()` | Identity-enrichment failures degrade to empty objects — is that the right failure semantics, and who notices? |
| DUP-04 | `_shared/matching_provider.py:67` | The per-track scaffold (skip-no-id, NO_METADATA short-circuit, failure capture) is re-implemented by every connector's `_match_by_artist_title` | The template-method boundary may be drawn at the wrong altitude — exactly the question Apple Music (a third implementor) will force |
| DUP-05 | `spotify/models.py:119` | Two paginated models duplicate the Web API PagingObject envelope | Minor, but pagination is where dead-ID/churn handling lives — touching it interacts with resolution flows |

(Two further findings, DUP-03 `base_repo.py` conditions-loop and DUP-06 CLI batch-rendering, are outside identity scope — listed here only so you know the full set exists in the audit record.)

Work the phases in order; each produces a written artifact. Use subagents for breadth, but **read load-bearing primary sources yourself in the main thread** (per `~/.claude/rules/investigation-and-revert.md` — a subagent summary is a starting point, never the basis for a load-bearing claim).

### Phase A — Internal archaeology (ground truth before opinions)

- Trace each resolution flow end-to-end with real file reading (the table above is your index; verify it, don't trust it).
- **Quantify the current state** from data, not vibes. Useful read-only queries against the dev/prod DB (user runs prod ones): mapping counts by `match_method` × confidence band; review-queue depth and accept/reject ratios; tracks with no primary mapping per connector; ISRC collision counts in `connector_tracks`; fallback-resolution rates from `confidence_evidence`.
- Git archaeology on the matching engine: `git log --follow` on `domain/matching/*` and the inward resolvers — the three-scenario Spotify design and the ISRC-suspect check both look like scar tissue; find the incidents that produced them.
- Inventory every place identity is *asserted* outside the matching engine (e.g., `Track.has_same_identity_as`, dedup logic in play import, merge service) — the engine being good doesn't help if bypasses are common.

### Phase B — External best practices (2026)

Research areas — find current primary sources, prioritize 2024–2026 material, and note what's evergreen vs hype:

- **Entity-resolution fundamentals** as applied to catalogs: deterministic vs probabilistic matching, survivorship/golden-record patterns, observation-ledger / claims-based identity models, confidence decay over time.
- **Music-domain identity specifically**: MusicBrainz's MBID model (recording vs release vs work — which granularity maps to mixd's "track"?), ListenBrainz's MBID-mapper (they solve exactly this problem at scale — their matching pipeline and its public lessons), ISRC's documented failure modes (industry sources), how open-source library managers (beets, Navidrome ecosystem, Lidarr) handle cross-service identity.
- **Fault-tolerance patterns** for identity systems: how to represent "we used to believe X" (mapping supersession), idempotent re-resolution, quarantine vs auto-heal, human-review queue design that doesn't rot.
- Cross-check anything that will become a recommendation against at least two independent sources.

### Phase C — Provider deep dives (primary docs only, fetched fresh)

For each provider, answer the same question set: *What identity surface does it expose? How stable are its IDs? What batch/lookup affordances exist for ISRC/MBID? What are the rate limits and auth constraints? What are its documented identity quirks?*

- **Spotify**: current Web API docs on track relinking/`linked_from` semantics post-2026 API changes, market-dependence of IDs, ISRC search reliability, batch lookup limits. Reconcile with what the codebase already learned (REDIRECT scenario, deprecated-field guards in `spotify/models.py`).
- **Apple Music** (the integration target — go deepest):
  - MusicKit / Apple Music API identity model: **catalog IDs are storefront-scoped** — verify current semantics and what that does to a single `connector_track_identifier` per track.
  - ISRC lookup support (`filter[isrc]`), the `equivalents` relationship (clean/explicit, storefront equivalents), library IDs vs catalog IDs.
  - Auth: developer token (ES256 JWT) + Music User Token flow — what's feasible for a self-hosted multi-user app; token lifetimes; what the existing `auth_method="coming_soon"` gate and `error_classifier.py` skeleton anticipated.
  - Rate limits and batch sizes; what a play-history / library import even looks like (Apple's API surface for listening history is famously thin — verify what's actually possible in 2026 vs what requires user data export files, and note the identity implications of each path).
- **Last.fm**: confirm the no-stable-ID reality and the current state of its API (the codebase already has an error classifier with deprecated-endpoint codes); what identity signals exist beyond artist/title (MBIDs in responses — how reliable today?).
- **MusicBrainz**: lookup/browse rate limits, MBID stability guarantees, recording-vs-release disambiguation, whether scheduled re-validation (the v1.0.3 sketch) is realistic at this project's scale.
- **Breadth ring — Tidal, Deezer, SoundCloud, YouTube Music** *(survey depth, ~a page each; added 2026-07-02 for the omni-integration assessment)*: the same question set as above, plus a **curation-capability lens** — what does each expose for likes/favorites, playlist read/write, play history, and audio features/metadata? Structural facts (identity model, ID stability, capability existence) are the research target; rate limits, auth-program terms, and batch sizes are perishable — record them with a "re-verify at implementation" marker rather than as load-bearing findings (Tidal's developer program in particular has churned repeatedly).

### Phase D — Synthesis: the design-space memo

Produce `docs/backlog/identity-resolution-design-space.md` containing:

1. **Failure-mode taxonomy**: every way identity goes wrong here, each tagged with evidence (file:line, data query result, incident from git history, or provider-doc citation) — and which are *currently unhandled*.
2. **Design space, not a design**: 2–4 coherent directions (e.g., "harden in place", "boundary-validation + supersession ledger", "global identity layer per v1.0.3", …) with costs, risks, migration implications, and what each does/doesn't fix from the taxonomy. Explicitly mark where the rule-of-three says *don't* abstract yet.
3. **Apple Music readiness assessment**: what the current connector pattern (BaseMatchingProvider + InwardTrackResolver + play resolver) can absorb as-is, what Apple's identity model strains (storefront IDs, equivalents), and what's genuinely unknown until implementation.
4. **Verification plan** (required even for a doc-only memo): name the existing regression net (unit: `tests/unit/domain/matching/*`, connector matching tests; integration: `test_identity_resolution_pipeline.py`, `test_cross_source_identity.py`, `test_identity_map_behavior.py`; diagnostic: `test_stale_id_fallback.py`) and the **characterization tests that must exist before any refactor** of flows the net doesn't cover.
5. **Connector-contract scalability assessment** *(added 2026-07-02)*: judged against the full capability matrix (committed ring + breadth ring), what would an omni-integration future (8–10 services) demand of `BaseMatchingProvider` / `InwardTrackResolver` / the play-importer pattern — and where does DUP-04's template-altitude question resolve vs. stay deferred? AHA discipline holds: map the design space and name the forcing points; do not pre-extract abstractions. Include the capability matrix itself — it doubles as evidence for sequencing the unscheduled connectors (Tidal / Deezer / SoundCloud) when their time comes.
6. **Backlog stories** for the recommended next steps, in `backlog-format` (story/decisions/spec/tests, user-goal-rooted), filed to `docs/backlog/unscheduled.md`.

---

## 4. Guardrails

- **Non-prescriptive mandate**: the user explicitly wants the design space mapped, not a winner crowned. Present trade-offs; flag a lean if the evidence is strong, but every load-bearing claim must trace to a primary source or repo evidence.
- **Architecture invariants hold**: domain stays pure; matching decisions stay in `domain/matching`; providers stay logic-free; any proposed structure must fit Interface → Application → Domain ← Infrastructure.
- **AHA discipline**: two connectors (Spotify, Last.fm) share patterns today; Apple Music makes three. Three real consumers is exactly when extraction becomes legitimate — but only for duplication that actually exists, not anticipated.
- **No dependency or schema proposals without the verification ritual**: actual+planned usage grounded in quoted code, alternatives actually researched, primary docs read in main thread, test plan named.
- **Read-only**: no prod writes; prod data queries go through the user.

## 5. Session log seeds (context from the 2026-06 tighten campaign)

- A dead structural ISRC validator (`validate_isrc_structure` + `ISRCValidationResult`) was deleted — only the duration-based `assess_isrc_match_reliability` survives. Structural ISRC validation had zero production callers; if your design wants it, that's a finding worth making explicit (validate-at-boundary was the floated-and-deferred idea).
- `vulture_whitelist.py` documents which matching surfaces are tested-but-unconsumed; check it before assuming a method is production-live.
- 4 dead API endpoints were removed; the **review endpoints survive deliberately** (v1.0.x review UI claim) — the review-queue UX gap is roadmap-acknowledged, not an oversight.
- Coverage gate is 80% (currently ~85.4%); fast suite <90s; integration tests use testcontainers PostgreSQL — one integration-touching pytest invocation at a time.
