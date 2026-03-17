---
paths:
  - "docs/backlog/**"
  - "docs/completed/**"
  - "docs/user-flows.md"
---
# Backlog & Spec Format Rules

## Directory Structure
- `docs/backlog/README.md` — master roadmap: version matrix, infrastructure readiness, tech decisions
- `docs/backlog/v0.X.x.md` — one file per minor version series (may contain multiple patch versions)
- `docs/backlog/unscheduled.md` — ideas without version assignment
- `docs/completed/` — archived version files + index after all stories ship

## Version File Format
Header: `# v0.X.x: [Initiative Name]`
Sub-versions: `### v0.X.Y: [Feature Milestone] (Vertical Slice N)`
Each sub-version includes: **Goal**, **Context**, **What this unlocks**, **Key tech choices**

## Story Format (mandatory fields, this exact order)
- [ ] **Story Title**
    - Effort: XS|S|M|L|XL|XXL
    - What: One-sentence description
    - Why: Business/architectural justification
    - Dependencies: version refs or story titles (None if none)
    - Status: Not Started | In Progress | Blocked | Completed (YYYY-MM-DD)
    - Notes:
        - Implementation guidance, schema changes, test expectations

## Epic Grouping
`#### [Epic Name] Epic` — groups related stories. Epics have no status or effort of their own.

## Master README
Current Version/Initiative at top. Version matrix: Version | Goal | Status | Details. Status: ✅ Completed | 🔨 In Progress | 🔜 Not Started. Infrastructure readiness matrix + tech decision records.

## Lifecycle
- **Complete story**: check box `- [x]`, set `Status: Completed (YYYY-MM-DD)`, update README matrix
- **Complete version**: move to `docs/completed/`, update completed/README.md index, update backlog README

## Effort Sizing (relative, NEVER time-based)
XS: trivial | S: small, 1-2 areas | M: cross-module | L: architectural, ≥3 subsystems | XL/XXL: break down further

## User Flows Spec (`docs/user-flows.md`)
- User stories use `**US-AREA-N**:` prefix with Given/When/Then acceptance criteria
- Version annotations `(v0.X.x)` on stories match backlog version files
- Prefer marking stories superseded (with a note) over deleting — preserves the decision trail

## Conventions
- Always convert relative dates to absolute (e.g., "Thursday" → "2026-03-20")
- New ideas → unscheduled.md first, version file when committed
