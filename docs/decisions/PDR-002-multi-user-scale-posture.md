# PDR-002: Multi-User Scale Posture

**Status**: Open — rings 0–1 decided (2026-07-02); rings 2–4 held open with triggers

**Question**: At what evidence thresholds does mixd activate each successive ring of multi-user machinery — open registration, unanchored cross-user sharing, trust systems, federation?

**Why it matters / blast radius**: The governance research's central economic finding is that multi-user trust machinery built ahead of its population is either inert or actively dangerous (a review economy below ~100 active users is capturable by three sock accounts). Building early wastes effort *and* creates attack surface; building late under growth pressure produces panic architecture. The resolution is a pre-agreed ladder: each ring names its trigger, its mechanism, and what must already be true.

**Decide-by trigger**: This PDR never "decides" once — each ring has its own trigger below. What's held open is the *shape* of rings 2–4, revisited when their triggers approach.

**Do-nothing default**: Rings 0–1 (already specced/decided) cover everything up to a few hundred invited users indefinitely. If mixd stays at ~10 users, this PDR simply never advances — and cost nothing.

## The ladder

| Ring | Population | Trigger to activate | Mechanism | Status |
|---|---|---|---|---|
| **0 — Trusted circle** | ≤ ~50, self-hosted / invited friends | (live today) | Admin is root; per-user identity curation; sharing via possession-scoped links | ✅ Current state |
| **1 — Invite-tree instance** | ~50–500, hosted, invite-only | Hosted instance launches | Invite provenance (Lobsters pattern, specced v1.1.3); default-followers visibility; **no public vanity metrics**; anchor-only public resolution (S1 fan-out, v1.2.0 revised); v1.1.0 anti-abuse suite | ✅ Decided 2026-07-02, specced |
| **2 — Open registration** | 500+ strangers | Sustained invite-queue pressure AND owner accepts abuse-handling duty | All ring-1 + rate/aggregate caps enforced (specced), operator-corrections path (Odesli model), identity cost for any privileged action (vouching, not activity thresholds) | ⏸ Open — may never activate; invite-only is a legitimate permanent answer (Lobsters, 12+ years) |
| **3 — Unanchored cross-user sharing / trust machinery** | any, once sharing long-tail content matters | A real feature needs sharing *unverifiable* identities AND ring-2 identity cost exists | v1.0.3's parked TrustScore, gated on invite-tree cost — never activity-earned reputation | ⏸ Parked in v1.0.3 spec with this exact trigger |
| **4 — Federation** | multiple instances | Two real instances want to share; ActivityPub item (unscheduled, XL) gets scheduled | Instance = trust unit; cross-instance assertions subscribable + anchor-verifiable, never auto-trusted (Matrix policy-room / Bluesky labeler shape) | ⏸ Open; cheap prep already adopted: shared assertions carry anchor evidence so remote instances can re-verify rather than trust |

## Dimensions (for the open rings)

| Dimension | Stay invite-only forever | Open registration | Federate instead |
|---|---|---|---|
| Growth | Slow, high-trust (Lobsters-shaped) | Fastest, most abuse | Growth via *instances*, not users — communities bring their own admin |
| Moderation labor | Near zero (structural) | Real and unavoidable — someone answers reports | Per-instance (each admin moderates their own) |
| Toxicity risk | Minimal — circle shape self-selects | The classic path to a network you didn't intend | Bounded per instance; defederation as sanction |
| Fit with "friends sharing curation" | Perfect | Weak — strangers aren't the stated goal | Strong — friend circles as instances |

## Evidence ledger

| Date | Type | Source | What it shows | Supports | Strength/caveat |
|---|---|---|---|---|---|
| 2026-07-02 | [S] | MusicBrainz live stats (verified: 430,843 edits vs 21,699 votes/7d) | Even a 25-year commons runs on default-accept, not peer review — vote-based governance needs mass it never fully gets | Rings 2–3 skepticism | Primary, verified in session |
| 2026-07-02 | [S] | Discogs governance history (their own account) | Gatekeeper review collapsed at ~10k submitters/500 mods; small-scale answer was moderator oligarchy | The ladder concept itself | Best-documented scale threshold found |
| 2026-07-02 | [S] | Lobsters (verified: public invite tree, "helps identify voting rings") | Invite-only + provenance + transparent moderation works for 12+ years at thousands of users with no paid staff | Ring 1 as permanent posture | Direct precedent for "never activate ring 2" |
| 2026-07-02 | [R] | Douceur, *The Sybil Attack* (2002) | Without identity cost, any vote-weighted mechanism is owned by whoever scripts signups | Ring-3 gating on identity cost | Canonical result |
| 2026-07-02 | [R] | Wikipedia ArbCom record (extended-confirmed gaming) | Activity-earned trust thresholds are farmable by motivated attackers | Never gate on activity counts | Documented enforcement cases |
| 2026-07-02 | [S] | FactGrid (~700 vetted real-name researchers) | Identity-vetted membership sustains a knowledge commons at exactly ring-1/2 scale without voting machinery | Ring 1–2 mechanism choice | Closest live small-scale analog |
| 2026-07-02 | [S] | Letterboxd/TMDb; Songlink (G3, verified) | Beloved sharing products run identity with zero user curation and zero/near-zero moderation staff — by removing gameable surfaces | Anchor-only sharing at every ring | The "easy solution" confirmed shipped |
| 2026-07-02 | [S] | OSM "Jewtropolis" (2h revert, weeks-later downstream reappearance) | Revert speed at source doesn't bound stale-cache exposure — TTLs are part of the abuse posture | Ring 1+ cache design | Applied to v1.2.0 CDN spec |
| 2026-07-02 | [A] | Mastodon growth-wave moderation strain; Bluesky 17× report growth | Open registration converts governance from structural to laborious, suddenly | Ring-2 trigger wording ("owner accepts the duty") | Directional but consistent across networks |

## What would change my mind

- **Toward opening registration**: real invite-queue pressure (people asking in, unmet); a co-admin willing to share abuse duty; ring-1 abuse tooling proven boring in practice.
- **Toward federation-first**: multiple friend groups independently self-hosting and asking to connect (this would *replace* ring 2 rather than follow it — and would fit the product thesis better than open registration).
- **Toward tearing rings down**: any sign the invite tree is producing in-group gatekeeping dynamics (the Lobsters model's known failure mode); evidence that no-vanity-metrics is starving legitimate discovery friends actually want.
- **Watch for**: [S] evidence from small federated networks (Matrix communities, small fediverse instances) on what actually breaks first at ring 4 — thin in current research.

## History

- 2026-07-02 — Opened. Rings 0–1 decided and specced (links-first sequencing in v1.1.x, no vanity metrics, invite-provenance permanent, anchor-only public resolution in v1.2.0, TrustScore parked in v1.0.3 with ring-3's trigger). Owner framing: "maybe 10 people ever use this, but if it takes off I want to be ready" — readiness defined as *this ladder*, not pre-built machinery. Full evidence base: [identity-governance-design-space.md](../backlog/identity-governance-design-space.md).
