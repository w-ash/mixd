# Completed Milestones

Detailed epics and task breakdowns for completed versions.
For the planning overview, see [../README.md](../README.md).
For active backlog, see [../](../).

| Version Group | File | Versions |
|---------------|------|----------|
| **v0.2.x** | [v0.2.x.md](v0.2.x.md) | v0.2.7 |
| **v0.3.x** | [v0.3.x.md](v0.3.x.md) | v0.3.0, v0.3.1, v0.3.2, v0.3.3 |
| **v0.4.x** | [v0.4.x.md](v0.4.x.md) | v0.4.0–v0.4.11 |
| **v0.5.x** | [v0.5.x.md](v0.5.x.md) | v0.5.0–v0.5.10 |
| **v0.6.x** | [v0.6.x.md](v0.6.x.md) | v0.6.0–v0.6.12 (+ v0.6.0.post1) |
| **v0.7.x** | [v0.7.0-1.md](v0.7.0-1.md) · [v0.7.2-3.md](v0.7.2-3.md) · [v0.7.4-5.md](v0.7.4-5.md) · [v0.7.6.md](v0.7.6.md) · [v0.7.7.md](v0.7.7.md) · [v0.7.8.md](v0.7.8.md) | v0.7.0–v0.7.8 (series archived whole, 2026-07-03) |

## Records & handoffs

Completed one-off records (handoffs, findings, migration notes) — see the `backlog-format` rule's One-off records lifecycle.

| Record | What it was |
|--------|-------------|
| [explicit-any-cleanup-batches-1-3.md](explicit-any-cleanup-batches-1-3.md) | Domain + App XS Any-cleanup (448→385) — early batches before v0.6.12 |
| [uuid-type-migration.md](uuid-type-migration.md) | int→UUID FK migration + review-candidate persistence (2026-04-04) |
| [design-debt-review-handoff.md](design-debt-review-handoff.md) | Commission for the mid-2026 design-debt audit; deliverable stays active as [../design-debt-findings.md](../design-debt-findings.md) |
| [v0.8.8-handoff.md](v0.8.8-handoff.md) | Orientation handoff for the v0.8.8 import/sync web UI |
| [identity-resolution-research-handoff.md](identity-resolution-research-handoff.md) | Commission for the 2026-07 identity research; design-space memos stay active in the backlog root |
| [import-sync-rearchitecture-handoff.md](import-sync-rearchitecture-handoff.md) | Phases 1–4 build log + phases 5–6 work order, shipped as v0.8.7/v0.8.8 |

---

## Pre-v0.2.7 Changelog

Versions before detail files were introduced:

### v0.2.1: Like Sync
Sync Spotify likes → Narada → Last.fm with checkpoint-based resumable operations.
- Import Spotify Likes | Export to Last.fm | Database Checkpoints

### v0.2.2: Play History
Import complete listening history from Spotify GDPR exports and Last.fm API.
- Spotify GDPR JSON Import | Last.fm History Import | Enhanced Track Resolution

### v0.2.3: Clean Architecture Foundation
Rebuilt codebase with Clean Architecture + DDD for reliability and future growth.
- /src Structure Migration | Service Layer Reorganization | Matcher System Modernization | Workflow Node Architecture

### v0.2.4: Playlist Updates
Intelligent playlist automation with differential updates preserving metadata and ordering.
- Comprehensive CRUD Operations | Differential Update Algorithms | Playlist Diff Engine | UnitOfWork Pattern

### v0.2.5: Workflow Transformation Expansion
Filter and sort playlists based on listening history and play patterns.
- Play History Filter/Sort Nodes | Time Window Support | Import Quality Foundation | Database Performance Indexes

### v0.2.6: Enhanced Playlist Naming
Dynamic playlist names/descriptions using template parameters ({track_count}, {date}, {time}).
- Template-Based Naming | Parameter Substitution | Create/Update Node Support
