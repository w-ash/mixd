---
paths:
  - "docs/backlog/**"
  - "docs/completed/**"
  - "docs/web-ui/01-user-flows.md"
---
# Backlog Format

"The user wants X so that they can Y" is the atomic unit of planning. Every design decision traces back to a user goal.

## File Structure

- `docs/backlog/v0.X.x.md` — one file per minor version.
- `docs/backlog/README.md` — roadmap, version matrix, tech decisions.
- `docs/backlog/unscheduled.md` — uncommitted ideas (start new ideas here).
- `docs/completed/` — archived minor series + index.

## Version File

Each `v0.X.x.md` opens with a **User Workflow Context** — what the user does today and where it breaks down — that every sub-version traces back to.

Each sub-version (`v0.X.Y`) ships a **complete, manually testable slice of user value**: the user can do something end-to-end they couldn't before. No manual test scenario = plumbing; fold it into a sub-version that ships UI/API/CLI.

Sub-version sections: **Goal** · **Context** · **What this unlocks** · **Persona** · **Key Design Decisions**.

## Story Format

<example>
- [ ] **Listening History Schema & Repository**

    **Story**: The user wants to see when and how often they've played
    each track so they can build playlists like "loved but forgotten"
    (starred tracks unplayed in 6 months). Years of Last.fm scrobbles
    must come in with original timestamps — a 2019 play shows as 2019,
    not the import date.

    **Decisions**:
    - `played_at` preserves source timestamp, not import date —
      flattening would make "unplayed in 6 months" return wrong results.
    - Append-only: the user wants an accurate play count; editing
      history would undermine trust.
    - Batch insert at scale (Last.fm exports hit 50k+ scrobbles).

    **Spec**:
    - `play_history` table: `id`, `user_id` (FK→users), `track_id`
      (FK→tracks, ON DELETE CASCADE), `played_at`, `source`, `created_at`
    - Indexes on `(user_id, track_id, played_at)` and `(user_id, played_at)`

    **Tests**:
    - (integration) `played_at` preserved from source
    - (integration) Batch insert of 10k rows succeeds
    - (integration) ON DELETE CASCADE: deleting track removes history

    Effort: M | Dependencies: Track Domain Model | Status: Not Started
</example>

**Story** = who/what/why, with enough detail for in-flight judgment. **Decisions** = non-obvious choices, user-rooted rationale. **Spec** = schema/API/technical. **Tests** = verification at the right layer. Infra stories use **Story** to explain the user-facing capability they enable.

## Readability (problem-first, dual-reader)

An item is read by two audiences: a **human** skimming the roadmap (wants the user need fast, the solution left open) and **Claude** picking it up cold (wants the technical anchors or it re-derives them). Serve both by **altitude, not omission** — keep the *why* up top and demote the *how* down, never blend them.

- **Lead with the problem, never the mechanism.** The title and first Story clause name a persona need ("the *‹persona›* wants *‹X›* so they can *‹Y›*"), not a feature, file, or function. The `_User goal:_` line, if used, is hoisted to the lead — not stranded at the bottom where a skimmer never reaches it.
- **Resolve lead-vs-grounding by altitude.** Problem-first prose lives in **Story/Decisions**; every locked identifier, path, signature, endpoint, and magic number lives in **Spec/Notes**. Give Claude the anchor it needs to act, but never weave a constant into a narrative sentence.
- **Decisions = verdict + "because *‹user impact›*".** Lead each bullet with the choice, then the user-rooted reason. A bullet with no user-facing "because" is Spec material — move it.
- **State each load-bearing fact once.** "Already shipped in vX.Y" → Dependencies (or one `Already in place (vX.Y): …` line); reference it elsewhere, don't restate it. Repetition (the same "ported from X" / "already built" note 5×) is the dominant readability tax — and never weave past-tense archaeology into forward-looking planning prose.
- **Anchor as direction, not a frozen manifest.** Name the real ids/tables/endpoints once; frame constants (widths, TTLs, thresholds, rate limits, model versions) as "starting point, revisit" — especially in unscheduled items, where deep specs wait until scheduling. Decide before filing: no unresolved "X or Y" presented as spec; drop superlative roadmap framing.
- **Make it skimmable.** Split walls of text into Story/Decisions/Spec/Tests; bullet blast-radius / tool-coverage lists instead of inline run-ons; bold the load-bearing constraint at the *start* of its bullet. Push doc-bug asides and incident IDs to a pointer into the design/findings doc. Group pure hygiene/refactor items under a labeled subhead so skimmers can skip them and Claude can still find them.

## Post-Deploy Revisions epic (required per feature)

Every feature file reserves a placeholder epic at the bottom — **Post-Deploy Revisions** — that accumulates work discovered during prod testing. Starts empty; entries are added as each revision ships (scheme in `version-management`). Each entry maps to a `<feature>.<N>` tag; the conventional-commit prefix (`fix:` / `feat:` / `refactor:`) carries the nature of the work.

<example>
#### Post-Deploy Revisions

- [x] **v0.7.6.1** (2026-05-02) — fix Spotify rate-limit backoff on playlist pagination

    **Why:** Prod testing on a 12k-track playlist hit a 429 that wasn't retried; import stalled at 3,200 tracks.
    **What:** Exponential backoff + capped retry in `spotify/operations.py`. Regression test covers the 429 + eventual-success path.

- [x] **v0.7.6.2** (2026-05-05) — surface pre-import track-resolution preview

    **Why:** Real-world imports revealed that low-overlap playlists surprised users with many unresolved tracks.
    **What:** New `POST /connectors/spotify/playlists/import/preview` + dialog column. Originally a v0.7.6 epic; shipped as a revision because the need only became concrete with prod data.
</example>

When the feature closes (`🚀 Shipped` → `✅ Completed`), the epic freezes as the full revision history.

## Lifecycle

### Story states

`- [ ]` pending → `- [x]` with `Status: Completed (YYYY-MM-DD)`. `/ship` reconciles first-ship changes against planned stories; revisions land in Post-Deploy Revisions (unless one happens to close a pre-existing story).

### Feature status (README version matrix)

| Status | Meaning | Set by |
|---|---|---|
| `🔜 Not Started` | Planned, no code yet | Manual |
| `🔨 In Progress` | Actively being built | Manual when implementation starts |
| `🚀 Shipped` | Deployed to prod, may still revise | `/ship` on first ship |
| `✅ Completed` | User confirmed stable, moved on | Manual after user's OK |

**`/ship` never writes `✅ Completed`.** That transition only happens when the user explicitly confirms a feature is stable.

### Minor-series archival

`git mv docs/backlog/v0.X.x.md docs/backlog/completed/` only when every feature in the series is `✅ Completed` — never per-feature.

## Conventions

- **Effort**: XS trivial | S 1-2 areas | M cross-module | L architectural | XL/XXL break down. Never time-based.
- **Dates**: absolute ("Thursday" → "2026-03-20").
- **New ideas** land in `unscheduled.md` first.
- **User flows** (`docs/web-ui/01-user-flows.md`): numbered flow sections (`## N. Area` → `### N.M Title`), each with **Trigger / Steps / Backend calls / Edge cases** and a per-endpoint Status token. (Not a `US-AREA-N` prefix.)
