# Identity Governance — Design-Space Memo

**Date**: 2026-07-02 · **Companion to**: [identity-resolution-design-space.md](identity-resolution-design-space.md) (extends its §2-D3) · **Status**: research deliverable, no code changed

**The question** (from the user): how much should mixd lean on its own internal identity solution vs an external authority like MusicBrainz — and how does multi-user change that? Constraints: no single human arbiter, no hired moderators, bad actors must not be able to corrupt shared state; per-user curation is power-user behavior; sharing between users needs *some* shared resolution. Mandate: map the possibility space, grounded in systems that already work, in music and adjacent spaces.

**Method**: three research passes (governance economics of identity commons; arbiter-less trust architectures; shipped music/media products with zero user curation), all primary-sourced 2026-07-02. Load-bearing claims re-verified in the main session: MusicBrainz voting mechanics + live stats, Letterboxd's TMDb outsourcing (their own help center), Lobsters' invitation tree. Full sourcing in the session's research reports; key citations inline.

---

## 0. The four findings that shape everything

1. **Nobody ships user-curated *shared* music identity.** The category-defining share-link product (Songlink/Odesli) is a fully automated resolver: platform ID in → ISRC/metadata matching → cached per-service fan-out; corrections are "email the operator" plus rights-holder page claiming — no community curation exists and no one misses it. Every shipped product that *does* let users curate identity (Plex Fix Match, Roon album edits, Jellyfin Identify, Navidrome tags, Soundiiz matching rules — and mixd's own mappings) scopes it **strictly locally, never published as shared truth**. The one system that shares name-keyed identity across all users — Last.fm — is the industry's cautionary tale, and its staff confirm the failure is structural ("Last.fm cannot currently distinguish between artists of the same name"). The lesson is precise: the failure mode is *non-unique keys as shared identity*, not the absence of curation.

2. **The architecture mixd's social layer already imitates runs identity with zero metadata staff.** Letterboxd, verified from its own help center: "Letterboxd sources all film-related data from The Movie Database (TMDb)"; users who spot errors are told to "create an account at TMDb and follow their guidelines for adding or updating film details." Letterboxd's crew touches identity only at three seams — activity-preserving merges of duplicates, retention of locally-referenced entries when TMDb deletes them, and an orphaned-content export when upstream removal is unavoidable. The cost of total outsourcing is not wrong identity; it is **upstream churn**, absorbed by those three mitigations.

3. **No arbiter-free governance system exists at any scale — but the arbiter can be made rare, mechanical, and accountable.** Every commons studied keeps a small human backstop: MusicBrainz pays a style BDFL and employee account-admins; Wikidata has WMDE emergency access; Discogs has a paid database-support team; OSM has 26 volunteer DWG members; even certificate transparency needed Google to enforce consequences on Symantec. What the successful systems optimize is *how rarely the backstop fires*: they displace routine governance into mechanism (default-accept-with-expiry, deprecate-don't-delete, auto-revert thresholds, earned-and-revocable capability, full history + cheap revert). For mixd this reframes the user's constraint: the goal is **no per-decision human arbiter**, not zero humans — the instance admin already holds root and is the honest backstop.

4. **Community voting/reputation demonstrably fails at mixd's scale.** MusicBrainz's live stats (verified 2026-07-02): 430,843 edits vs 21,699 votes in 7 days — **~5% of edits receive any vote even at 3,300 weekly active editors**; the system works because unopposed edits auto-apply, not because peers review. Stack Exchange must lower its privilege thresholds on small sites because the normal ones don't function. Discogs' own history dates the collapse of small-scale gatekeeping precisely (2008: ~10,000 submitters per ~500 moderators) — and its pre-collapse answer for the 50–500-contributor era *was* a hand-picked moderator oligarchy. Below ~100 active users, a review economy is either inert or capturable — three sock accounts *are* a MusicBrainz-style unanimous quorum, and MB's own instant-apply-on-3-votes rule would invert into an attack accelerator. The Sybil result (Douceur 2002) is the bedrock: without identity cost, any vote-weighted mechanism is owned by whoever scripts the most signups.

---

## 1. Threat model, grounded in mixd's actual deployments

Three deployment contexts with different trust physics, times two data classes (per-user assertions; publicly-consumed resolution):

| Context | Who can write | Trust reality | Sybil cost |
|---|---|---|---|
| **Self-hosted, small N** (5–50: household, friends) | Invited users | Admin already has DB root; per-user identity governance is theater here | "Convince the admin" — Douceur's trusted certification, essentially solved |
| **Hosted instance, invite/open registration** (100s–1000s; personas: Tinkerer + Casual Enthusiast sign up) | Strangers | The dangerous configuration — any vote/consensus-weighted shared identity is structurally gameable | Near zero when registration is open; v1.1.3's invite system (with "Invited by" provenance) is the existing cost lever — Lobsters runs exactly this ("The full user tree is public… helps identify voting rings," verified) |
| **Federation** (ActivityPub item, XL, deferred) | Other instances | Instance = trust boundary; defederation = sanction (Mastodon precedent); assertions cross instances only by subscription (Matrix policy-room model) | Per-instance, not per-user |

**The canonical attack** (named in v1.2.0's own spec): a malicious sharer crafts data so a public track link resolves to the wrong song on the viewer's preferred service. Two variants: (a) corrupt the shared resolution store; (b) poison inputs so automated resolution picks wrong. Secondary threats: identity-store spam (fake tracks/artists at scale — MusicBrainz's actual vandalism pattern is fabricated releases used as containers for streaming-link spam), and the **stale-snapshot problem**: OSM's "Jewtropolis" vandalism was reverted at source in 2 hours, yet a downstream consumer shipped the stale label weeks later — directly relevant because v1.2.0 specs CDN-cached share pages (`s-maxage=3600`); cache TTLs bound vandalism exposure *after* revert.

**A constraint the research surfaced**: mixd has no audio files, so acoustic fingerprints (AcoustID/Chromaprint — the strongest content-derived anchor) are unavailable. mixd's verifiable anchor tuple is **ISRC + recording MBID + duration + per-platform IDs**, cross-checked against each other. Anchor *disagreement* (MBID metadata vs ISRC lookup vs duration) is the alarm signal, per the multi-perspective lesson from Web PKI (even external anchors need multiple independent observations).

---

## 2. The spectrum

Each point: mechanism, working precedent, gameability vs the picker attack, human labor, what it unlocks, migration from today.

### S0 — Per-user only (today)

No shared resolution; sharing degrades to text metadata (title/artist strings on a public page, no service buttons). **Precedent**: every local-first library manager pre-sharing. **Gameability**: none — nothing shared. **Labor**: zero. **Unlocks**: nothing in v1.1/v1.2; the picker page can't exist beyond search-link fallbacks. Included as the baseline: note that v1.2.0's *search-link* fallbacks (`music.youtube.com/search?q=…`) are S0-compatible — a degraded picker ships with zero shared identity.

### S1 — Share-nothing / resolve-at-view (the Songlink model)

Public surfaces resolve services **at share/view time from platform affordances** — source platform ID → ISRC (`external_ids.isrc`) → per-platform lookups (Apple `filter[isrc]`, Tidal `filter[isrc]`, Deezer `/track/isrc:`) → MusicBrainz recording as tiebreak — cached as a **derived, disposable fan-out row**, never derived from any user's mappings. Per-user curation stays fully personal.

- **Precedent (strong)**: Songlink/Odesli built the category on exactly this data model; a decade of Soundiiz/TuneMyMusic operating per-transfer with no shared identity DB ("Manual fixes apply only to that specific transfer") confirms per-lookup resolution is commercially sufficient. Odesli's failure mode is version drift (remaster/deluxe substitution), almost never wrong-*song*; misses degrade to absent/dead buttons absorbed by the viewer.
- **Gameability vs the picker attack**: excellent. The resolution never reads user-writable state — there is nothing to corrupt. Variant (b) shrinks to "poison the canonical track's own metadata," which only affects the attacker's own shares (tracks are per-user rows) and is bounded by anchor cross-checks.
- **Labor**: near zero; the Odesli correction model (report → operator fixes the cached row; instance admin = operator) is the backstop.
- **Unlocks**: all of v1.2.0/v1.1.3's per-track service buttons. **Limitation**: derived cache only — contributes nothing to cross-user dedup, social context on entity pages ("12 users love this track" needs cross-user *track* identity, not service links), or future recommendations.
- **Migration**: small. A `public_track_links` cache keyed by (track_id → anchor tuple → per-service fan-out + resolved_at), populated at share time, refreshed on TTL/dead-link detection. Note it reuses the main memo's D1/D2 machinery (ISRC guards, supersession reasons) but requires neither.

### S2 — External-anchor-only sharing (the Letterboxd/TMDb architecture)

Cross-user assertions exist, but **only externally-verifiable ones cross user boundaries**: a track identity is shared iff anchored (verified recording MBID, platform-confirmed ISRC, platform-asserted successor). Everything unanchored stays per-user. MusicBrainz plays TMDb's role: the community-governed external commons where contested identity is arbitrated — by *their* 25-year-old process, not mixd's.

- **Precedent (strong)**: Letterboxd (verified: total outsourcing, zero metadata staff, "fix it on TMDb"); Plex ("Plex Music starts with… MusicBrainz", Fix Match is local, durable fixes go upstream — "you help everyone else in the future, too"); Jellyfin/Navidrome (MusicBrainz-anchored, local overrides). The **contribute-back variant** is shipped practice, not idealism: Plex explicitly routes durable corrections to MusicBrainz; upstream edits propagate back in ~36h (Plex) / ~30h (Letterboxd).
- **Gameability**: to corrupt shared identity you must corrupt MusicBrainz — a larger, monitored, older target with its own (imperfect but real) governance. Two honest caveats from the evidence: (i) MB **fails open** at low attention (~5% vote coverage; unopposed edits auto-apply) — so "MB-verified" must mean *mixd re-checked the claim against MB's current state*, not "a user said MB says"; (ii) Last.fm-sourced MBIDs must never count as anchors (main memo FM1d/FM7c — type-confused, merge-stale).
- **Labor**: the Letterboxd triad, mostly mechanical: activity-preserving merge handling for upstream MBID merges (301 redirects — the machinery the main memo's D2 supersession schema already plans), retention of locally-referenced identities when upstream deletes, and export of orphaned data. Occasional operator attention; zero per-decision arbitration.
- **Unlocks**: everything S1 does, plus cross-user track identity for social context, cross-user dedup on shared surfaces, and recommendations groundwork. **Limitation**: coverage — the anchor reaches only anchored tracks. New releases lag in MB (the #1 documented no-match cause), and the long tail (Bandcamp-only, user uploads, SoundCloud-native) may never anchor. **Prod query pack Q14/Q15 measures exactly this** — what fraction of the real library carries ISRC/MBID today decides how big S2's unshareable remainder is.
- **Migration**: moderate — this is v1.0.3's `ConnectorIdentity` stripped of trust weighting: identity rows exist only when externally verified; `verified` is the *only* shared tier; everything else is per-user.

### S3 — Trust-weighted observation ledger (v1.0.3 as sketched)

Users' match decisions become observations with computed TrustScores; aggregation derives tiers (`verified/strong/weak/disputed`); shared truth emerges from weighted internal consensus with external verification as the top tier.

- **Precedent**: none shipped in this domain. The observation-*ledger* half has strong precedent (OpenSanctions, Senzing — as provenance/audit, per the main memo); the trust-*weighted crowd consensus* half is precisely what the governance evidence says fails at mixd's scale: the `strong` tier ("≥3 distinct trust-weighted observations agree") is structurally MusicBrainz's 3-unanimous-vote quorum — which at open-registration small N is **three sock accounts**. TrustScore's inputs (account age, validated-observation history) are the activity-threshold gates Wikipedia's ArbCom record shows patient attackers farm on purpose.
- **Gameability**: the weakest of the shared options at hosted-instance scale, unless identities carry real cost (invite-tree provenance helps; it's a price, not a wall).
- **Labor**: highest — trust computation, dispute states, and the review queue all need tending; disputes ultimately still land on a human (the backstop fires *per contested identity*, the thing the user wants to avoid).
- **What it buys over S2**: shared assertions for **unanchored** tracks (the long tail S2 can't reach) and resilience when the external authority is wrong. That's real value — but it's value for exactly the tracks where verification is impossible, i.e., where the system is most gameable. The evidence suggests buying that later, if ever, and only with identity-cost gates (admin vouching, invite provenance) rather than activity-earned reputation.
- **Migration**: the sketch's own plan — largest of all options, plus the main memo's warning that backfilling from today's corrupted confidences launders inflation into tiers.

### S4 — Community curation (MB-style voting inside mixd)

Full internal commons: users propose identity edits, peers vote, auto-editors emerge.

- **Precedent at small scale**: **negative, plainly stated**. Discogs 2001–2007 ran the 50–500-contributor era on a hand-picked moderator oligarchy (the model the user rejects); the voting system arrived only as a *scale adaptation* at ~10k contributors. FactGrid — the closest live small-Wikibase analog (~700 vetted, real-name researchers) — works via identity-vetted membership, not voting. No precedent exists for open community voting functioning at hosted-mixd scale.
- Rejected by the evidence for the foreseeable roadmap; revisit only if mixd somehow becomes a MusicBrainz-sized commons — at which point the answer is "contribute to MusicBrainz instead."

---

## 3. The composition the evidence supports (hybrid, sequenceable)

The spectrum points compose rather than compete — and the composition follows deployment reality:

1. **Public share surfaces (v1.2.0, v1.1.3): S1 now.** The picker needs the Songlink data model — anchor tuple + cached per-service fan-out, derived from platform affordances, never from user mappings. The attack degrades from "silent wrong resolution" to "absent button" (miss) or "visibly disputed link" (anchor disagreement). CDN caching gets the OSM lesson applied: short TTLs on resolution data (v1.2.0's `s-maxage=3600` is fine; anything longer needs revocation thinking).
2. **Cross-user identity where features actually need it (social context, dedup): S2.** Anchored-only sharing, Letterboxd mitigations (activity-preserving merge handling, retention-on-upstream-delete, orphaned export), read-time verification against MB (never trust a stored claim another user produced), contribute-back as the correction path for MB-fixable errors. Per-user mappings remain exactly what Plex/Roon/Navidrome keep: **local overrides that always win locally and never become shared truth.**
3. **Trust machinery: defer until an identity-cost substrate exists and a feature demands unanchored sharing.** If/when that day comes, gate on invite-tree provenance + admin vouching (Lobsters/Tournesol/FactGrid pattern — identity cost), not activity-earned reputation (Wikipedia's farmable gates), and keep the observation ledger's role as *provenance and candidate-generation for verification*, not as a voting mechanism.
4. **Federation-proofing (cheap now, expensive later)**: represent every shared assertion with its anchor evidence attached, so a future remote instance can *re-verify* rather than *trust*. "Instance is the trust unit; cross-instance assertions are subscribable overlays, auto-trusted only when anchor-verifiable" — the Matrix/Bluesky-labeler shape.

**Answering the user's headline question directly**: lean on MusicBrainz (plus ISRC/platform affordances) for **everything that crosses a user boundary**, and keep mixd's sophisticated internal machinery for what it's already good at — *personal* resolution quality. mixd's internal solution and MusicBrainz aren't competitors; they operate at different trust scopes. The internal engine decides what *you* see; external anchors decide what *others* are told. The one governance body mixd genuinely needs — someone to arbitrate contested canonical identity for the whole world — already exists, is 25 years old, survives on ~$500k/year with roughly one paid content person, and mixd's users can already appeal to it. The realistic residual human role in mixd is the Odesli one: an admin who occasionally fixes a reported cached link — rare, mechanical, per-instance.

---

## 4. Impact on the existing design space (main memo §2-D3, v1.0.3, v1.2.0)

- **v1.0.3's core principle is validated**: "External APIs are arbiter, users are observers" is exactly what the evidence supports — the sketch had the right instinct.
- **v1.0.3's `strong` tier and TrustScore are contradicted at current scale**: `strong` (≥3 trust-weighted observations) is a 3-sock quorum under open registration; TrustScore inputs are farmable activity gates. Recommended revision: collapse the shared-tier vocabulary to **verified / unverified** (unverified = per-user only, never publicly resolved); park TrustScore + `strong` until a real unanchored-sharing need arrives *and* invite-provenance identity cost exists. The ObservationLedger survives with a narrower job: append-only provenance + candidates-for-verification queue (this also kills the queue-rot risk — the queue's consumer is a verification worker, not a human).
- **v1.2.0's hard dependency on v1.0.3 can be relaxed**: the picker needs the S1 fan-out cache (a much smaller component) plus anchored deep links — not the full ledger + trust computation. This is a sequencing lean, not a decision: it would let track sharing ship before the cross-user identity layer.
- **v1.2.0's confidence-tier UI** maps cleanly onto S1/S2: checkmark = anchor-verified deep link; "best available" = unverified fan-out result; disputed/conflicting anchors = suppressed. Same UX, simpler substrate.
- **Main memo cross-references**: S1/S2 depend on main-memo D1 repairs (an anchor pipeline built on corrupted confidence/method data inherits FM1's garbage) and D2's supersession schema (platform-asserted successors, MBID merge redirects, and fan-out refresh all need "we used to believe X"). The governance answer strengthens, not replaces, that sequencing.

## 5. Follow-ups (proposed, not applied)

1. **Run prod Q14/Q15** (already in the query pack): ISRC/MBID coverage sizes S2's reach and the unanchored remainder — the single number that most shapes how much S3 pressure ever materializes.
2. **Spec revision candidates for v1.0.x.md / v1.2.x.md** (user's call; not edited by this pass): simplify v1.0.3 shared tiers to verified/unverified + narrow ObservationLedger's role (§4); note v1.2.0's relaxed dependency option (S1 cache).
3. **No new unscheduled stories filed**: the existing 2026-07 identity stories (confidence repair, ISRC guards, supersession ledger) are precisely the prerequisites this memo's S1/S2 build on; the S1 fan-out cache belongs in a future v1.2.0 spec revision rather than the unscheduled pool.
4. When Apple Music lands, its `filter[equivalents]`/storefront projection slots directly into the S1 fan-out as another platform affordance (main memo §3.2).

## 6. Main-thread-verified claims (this session)

- MusicBrainz: unopposed edits auto-apply after 7 days; 3 unanimous votes close early; voting requires 2-week-old account + 10 accepted edits (musicbrainz.org/doc/Introduction_to_Voting).
- MusicBrainz live stats 2026-07-02: 430,843 edits vs 21,699 votes in 7 days; 3,304 active editors (musicbrainz.org/statistics).
- Letterboxd: "sources all film-related data from The Movie Database (TMDb)"; corrections: "create an account at TMDb and follow their guidelines" (support.letterboxd.com help center API).
- Lobsters: "The full user tree is public and each user's profile shows who invited them. This provides some degree of accountability and helps identify voting rings"; all moderation actions public (lobste.rs/about).
- Full agent reports (G1 governance economics, G2 arbiter-less trust, G3 zero-curation products) with complete source lists are session artifacts; every recommendation-bound claim above is cross-checked in ≥2 of them or verified directly.
