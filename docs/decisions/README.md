# Product Decision Records (PDRs)

Slow-burn decisions that shape mixd's direction — held open on purpose, accumulating evidence until a **trigger** forces commitment. This is the antidote to two failure modes: deciding big things by vibes under deadline, and pre-building for scale that may never come.

**Not for**: decisions the backlog format already handles (feature-level Key Design Decisions), or anything cheap to reverse — just decide those inline. A PDR earns its file only when the decision is expensive to reverse, spans multiple versions, and benefits from evidence gathered over months.

## Format

One file per open question: `PDR-NNN-slug.md`.

| Section | What it holds |
|---|---|
| **Status** | `Open` · `Open — leaning: X (date)` · `Decided (date)` with rationale · `Superseded by PDR-NNN` |
| **Question** | One sentence. If it can't be one sentence, it's two PDRs. |
| **Why it matters / blast radius** | What gets built differently depending on the answer |
| **Decide-by trigger** | The *event* (not date) that forces the call — "before X ships", "when metric Y crosses Z". Until the trigger, the do-nothing default holds. |
| **Do-nothing default** | What happens if we never decide — the proportionality anchor ("maybe 10 people ever use this") |
| **Dimensions** | The axes of gives/takes, as a table: dimension × what each option gives/takes |
| **Evidence ledger** | Append-only, typed, dated (see below) |
| **What would change my mind** | Falsifiers per option — the strongest form of intellectual honesty, and the thing to actively look for |
| **Scale ladder** | What the answer looks like at ~10 / ~100 / ~10k users — readiness = documented triggers + reversible primitives, not pre-built systems |
| **History** | Dated notes as the opinion evolves |

## Evidence ledger conventions

Each row: `date | type | source | what it shows | supports | strength/caveat`

Evidence types, roughly strongest-to-weakest for *product* decisions (deliberately different from academic ordering — a shipped system surviving contact with real users usually beats a paper):

- **[S] Shipped system** — a real product solved/failed this in production (Songlink, Letterboxd, Obsidian, Discogs' 2008 governance collapse)
- **[D] Our data** — prod queries, diagnostics, user behavior in mixd itself
- **[R] Research** — peer-reviewed or rigorous institutional work (Douceur's Sybil result, Wikidata vandalism corpora)
- **[A] Anecdote / practitioner report** — forum threads, postmortems, community lore; directional, never load-bearing alone
- **[V] Vendor claim** — marketing until independently corroborated

Rows are never edited or deleted — append a correcting row instead. Perishable facts (rate limits, program terms) get a `re-verify` marker. Any session or research pass that surfaces relevant evidence appends a row; the deep research lives in its memo, the ledger row is the pointer + one-line takeaway.

## Why this format (the transferable part)

This is a deliberate hybrid of three industry practices: **Architecture Decision Records** (Nygard — context/decision/consequences, kept in-repo next to what they govern), **decision journals** (record the reasoning *before* the outcome, so you can audit your judgment rather than your luck), and **trigger-based deferral** ("last responsible moment" from lean — the discipline of naming the event that forces a call instead of deciding early for comfort). The evidence-typing borrows the spirit of GRADE from evidence-based medicine: not all support is equal, and writing down *why* you trust a source is most of the work. The scale ladder is the piece most worth stealing for platform PM work: it converts "are we ready for scale?" from anxiety into a checklist of named thresholds with pre-agreed responses.

## Open dossiers

- [PDR-001: Data ownership model](PDR-001-data-ownership-model.md) — sovereign server + exit rights vs local-first authority
- [PDR-002: Multi-user scale posture](PDR-002-multi-user-scale-posture.md) — which ring of multi-user machinery activates at which evidence threshold

Decided upstream of these (recorded in backlog specs, 2026-07-02): links-first sharing sequencing (v1.1.x), no public vanity metrics, invite-only as permanent trust primitive, external-anchor public resolution (v1.2.0), v1.1.0 trust machinery parked. Rationale in [identity-governance-design-space.md](../backlog/identity-governance-design-space.md).
