# Completed Milestones

Detailed epics and task breakdowns for completed versions.
For the planning overview, see [../README.md](../README.md).
For active backlog, see [../](../).

| Version Group | File | Versions |
|---------------|------|----------|
| **v0.2.x** | [v0.2.x.md](v0.2.x.md) | v0.2.7 |
| **v0.3.x** | [v0.3.x.md](v0.3.x.md) | v0.3.0, v0.3.1, v0.3.2, v0.3.3 |
| **Any Cleanup 1-3** | [explicit-any-cleanup-batches-1-3.md](explicit-any-cleanup-batches-1-3.md) | Domain + App XS (448→385) — early batches before v0.6.12 |

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
