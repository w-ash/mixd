---
paths:
  - "docs/backlog/**"
  - "docs/completed/**"
  - "docs/user-flows.md"
---
# Backlog Format

"The user wants X so that they can Y" is the atomic unit of planning. If you can't finish that sentence, you don't understand the feature yet. Every design decision must trace back to a user goal it serves.

## File Structure

- `docs/backlog/v0.X.x.md` — one file per minor version (may contain patches)
- `docs/backlog/README.md` — roadmap, version matrix, tech decisions
- `docs/backlog/unscheduled.md` — uncommitted ideas
- `docs/completed/` — archived versions + index

## Version File

Each `v0.X.x.md` starts with a **User Workflow Context** — what the user does today, step by step, and where it breaks down. Every sub-version traces back here.

### Sub-version Principle

Each sub-version (`v0.X.Y`) must deliver a **complete, manually testable slice of user value**. "Complete" means the user can do something end-to-end they couldn't before. "Manually testable" means you can verify it works by using the feature, not just running unit tests. If you can't describe a manual test scenario, the sub-version isn't delivering real value — it's delivering plumbing.

Infrastructure-only work (domain models, schemas) that can't be manually tested on its own belongs inside a sub-version that also ships the UI/API/CLI that makes it usable.

Sub-versions include: **Goal** (user need), **Context** (current workflow & workarounds), **What this unlocks** (user outcomes), **Persona** (specific quote), **Key Design Decisions** (user motivation first, then technical approach).

## Story Format

<example>
- [ ] **Listening History Schema & Repository**

    **Story**: The user wants to see when and how often they've played
    each track so they can build playlists like "loved but forgotten"
    (starred tracks unplayed in 6 months) and understand how their
    listening habits change over time. They have years of scrobble
    history in Last.fm that needs to come in with original timestamps
    — a track played in 2019 should show as played in 2019, not the
    day it was imported.

    **Decisions**:
    - `played_at` preserves the original source timestamp (Last.fm
      scrobble date, Spotify play date), not the import date — because
      the user wants to query by real listening date and flattening
      history to the import date would make time-based workflows
      ("unplayed in 6 months") return wrong results.
    - Append-only: play events are never updated or deleted. The user
      wants an accurate count ("tracks with 5+ plays") and editing
      history would undermine trust in the data.
    - Batch insert at scale: Last.fm exports can be 50k+ scrobbles.
      Must handle bulk insert without timeout or memory issues.

    **Spec**:
    - `play_history` table: `id`, `user_id` (FK→users), `track_id`
      (FK→tracks, ON DELETE CASCADE), `played_at` (DateTime, NOT NULL),
      `source` (String(32)), `created_at`
    - Index on `(user_id, track_id, played_at)` for per-track history
    - Index on `(user_id, played_at)` for date-range queries

    **Tests**:
    - (integration) `played_at` preserved from source, not defaulted
    - (integration) Batch insert of 10k rows succeeds
    - (integration) ON DELETE CASCADE: deleting track removes history

    Effort: M | Dependencies: Track Domain Model | Status: Not Started
</example>

The four sections serve distinct purposes:
- **Story** — grounds the agent in WHO wants WHAT and WHY, with enough detail to make judgment calls during implementation
- **Decisions** — non-obvious choices with user-rooted rationale. "We do X because the user wants Y and Z would break that." An agent reading only this section should understand the constraints.
- **Spec** — schema, API, technical detail
- **Tests** — verification scenarios at the correct layer

For infrastructure stories that don't directly face the user, **Story** explains what user-facing capability this enables.

## Lifecycle

- Complete story: `- [x]`, `Status: Completed (YYYY-MM-DD)`, update README
- Complete version: move to `docs/completed/`, update index

## Conventions

- Effort: XS trivial | S 1-2 areas | M cross-module | L architectural | XL/XXL break down further. Never time-based.
- Convert relative dates to absolute ("Thursday" → "2026-03-20")
- New ideas → `unscheduled.md` first
- User flows (`docs/user-flows.md`): `**US-AREA-N**:` prefix, Given/When/Then, version annotations
