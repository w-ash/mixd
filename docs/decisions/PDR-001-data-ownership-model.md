# PDR-001: Data Ownership Model

**Status**: Open — leaning: sovereign server + exit rights (2026-07-02, unconfirmed by owner)

**Question**: What does mixd promise about where a user's listening data lives — a server the user controls with real exit rights, or local-first authority on the user's own devices?

**Why it matters / blast radius**: This is the deepest architectural fork available. Local-first authority would touch persistence (relational Postgres → syncable device store), the connector layer (always-on polling → ??), multi-user/social features (which fight local-first), and the hosted-instance business model. Sovereign-server keeps everything built so far and adds portability features. It is also the emotional core of the product promise ("own my listening data") — getting the *promise* right matters as much as the architecture.

**Decide-by trigger**: Before any feature markets an ownership claim to non-technical users (hosted-instance signup copy, "your data" landing-page promises) — or before any architecture work that would be wasted under the other answer. Neither is imminent. Until then: build direction-neutral pieces only.

**Do-nothing default**: Current state persists — self-hosters have full ownership de facto; hosted users have an account on someone else's instance with ad-hoc export. Acceptable at ~10 users who are mostly the developer's circle; becomes a hollow promise if the hosted instance grows.

## Dimensions

| Dimension | Sovereign server + exit rights | Local-first authority |
|---|---|---|
| **Data continuity** (the decisive one so far) | Always-on server never misses a scrobble window | A sleeping device permanently loses plays — Spotify's recently-played holds ~50; gaps are unrecoverable |
| **Cross-device** | Free — the server is the sync point | Requires CRDT/sync engine; "hosted sync option" re-invents the server |
| **Vendor-death survival** | Requires the archive feature to be real (else hostage) | Native — data is already on your device |
| **Non-technical accessibility** | Hosted account + auto-archive; no setup | Excellent day-one feel; sync setup pain later |
| **Social/multi-user fit** | Natural | Fights it — shared surfaces need a reachable host |
| **Engineering cost from here** | ~One story (archive) + later polish | Effectively a rewrite |
| **Ownership *feeling*** | Abstract ("control + exit") — needs the archive to feel real | Visceral ("it's on my machine") |

## Evidence ledger

| Date | Type | Source | What it shows | Supports | Strength/caveat |
|---|---|---|---|---|---|
| 2026-07-02 | [S] | Obsidian (app + Sync service model) | Local-first + paid-sync-convenience is a beloved, commercially working ownership story — for *single-user, file-shaped, user-authored* data | Local-first (with caveats) | Strong for its domain; all three preconditions fail for mixd (relational graph, machine-generated data, social features) |
| 2026-07-02 | [S] | Spotify API surface (C2 research, verified) | Recently-played window ≈ 50 plays; missed polls = permanent data loss → continuous ingestion requires an always-on component | Sovereign server | Structural, not a preference — the strongest single argument |
| 2026-07-02 | [S] | Plex / Navidrome / Jellyfin (G3 research) | Self-hosted media servers are the shipped pattern for "own your library across devices" — server-authoritative, local clients cache | Sovereign server | Direct domain precedent |
| 2026-07-02 | [S] | Letterboxd account export; Apple/Spotify GDPR takeouts | Hosted services deliver credible "your data" stories via export completeness, not locality | Sovereign + exit rights | Export must be *complete and re-importable* to count — most takeouts fail the second half |
| 2026-07-02 | [A] | Obsidian community lore; local-first movement (Ink & Switch) | The ownership feeling drives real loyalty; "cloud = someone else's computer" resonates | Local-first | Directional; the feeling can be partially served by archive-on-your-storage |
| 2026-07-02 | [D] | mixd architecture (28 user-scoped tables, RLS, scheduled imports, workflows over 50k+ plays) | The system is deeply server-shaped; heavy queries + multi-tenancy assume Postgres | Sovereign server | Sunk-cost flavored — valid for "cost from here," not for green-field truth |
| 2026-07-16 | [D] | Prod probe ([play-import convergence findings §1](../backlog/play-import-convergence-findings.md)) | Production holds **zero** play rows — history has never been imported; the continuity the sovereign-server argument protects currently rests on nothing, and every un-polled scrobble window since the 2025-02 export is already lost from Spotify's side (Last.fm is the only live recorder) | Sovereign server (urgency) | Direct measurement; v0.10.1 schedules the recently-played poller that closes the window |

## What would change my mind

- **Toward local-first**: mixd pivots away from social/multi-user entirely; a mature off-the-shelf relational CRDT layer (the ElectricSQL/PowerSync class) proves production-ready for this shape at low integration cost; connectors move to push/webhook models that eliminate the polling-continuity argument; evidence that hosted-instance users *distrust* the server model enough to churn over it.
- **Toward sovereign-only (dropping even the archive)**: nobody ever uses the export (measure it); the hosted instance never materializes.
- **Watch for**: [S] evidence from apps with machine-generated continuous data (fitness trackers, scrobblers) that solved local-first well — none found yet; this is the missing comparable.

## Scale ladder

- **~10 users**: do nothing beyond the Continuous Personal Archive story (scheduled as v1.0.4, the last pre-social milestone) — it is direction-neutral groundwork every answer needs.
- **~100 users (hosted instance real)**: archive + documented import path become the marketed ownership story; add instance-migration (export bundle → any instance) so exit rights include "leave to a friend's server."
- **~10k users**: revisit local-first *client cache* (read-only offline library — the feel without moving authority); federation questions activate (PDR-002).

## History

- 2026-07-02 — Opened after owner raised the Obsidian model. Full gives/takes analysis in session; structural findings: continuity argument (scrobble windows) and cross-device-reinvents-the-server. Continuous Personal Archive story filed as direction-neutral groundwork. Owner explicitly deferring the decision; lean recorded is the researcher's, not the owner's.
