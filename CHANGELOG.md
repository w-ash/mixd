# Changelog

All notable changes to Mixd are documented here.

## [Unreleased]

### Bug Fixes

- post-migration cleanup from /simplify review
- checkpoint always-write-on-exit, Unset sentinel for cursor, commit-before-SSE
- async cleanup task leak and optimize test suite speed
- remove environment variable pollution and test fixture anti-patterns
- resolve LastFM e2e test failures and improve code quality
- resolve enricher key types and track repository test failures
- resolve 7 of 11 test failures through infrastructure fixes
- resolve type checking issues and format code

### Documentation

- update ARCHITECTURE.md and ROADMAP.md for current codebase
- Phase 2 complete - SKIPPED conversion utilities (differences are semantic)
- comprehensive modernization progress with context and Phase 11 test review
- update REFACTORING_PROGRESS.md for Phase 2 completion
- complete Clean Architecture migration documentation

### Features

- upgrade Vite 7→8 (Rolldown), adopt codeSplitting + tsconfigPaths
- workflow CLI CRUD + agent modernization (v0.4.11)
- CLI workflow runs create DB records + frontend design token consistency
- v0.4.10 — cross-source play history deduplication
- v0.4.9 — data integrity, identity resolution, test suite & type audit
- v0.4.8 — usability & self-explanatory interface pass
- connector track mapping management — relink, unlink, set primary
- v0.4.6 — track provenance & merge
- v0.4.4 — connector playlist linking + documentation restructure
- workflow fault tolerance, track invariants, and execution diagnostics
- add dynamic metric columns to workflow output tracks
- v0.4.3 — visual workflow editor, preview, versioning & diff
- v0.4.2 — run output persistence, pipeline strip, run-focused UI
- v0.4.1 — workflow execution, run history, live DAG status
- v0.4.0 — workflow CRUD, enricher nodes, workflow web UI, Spotify library/contains
- stale Spotify ID resolution with redirect detection and search fallback
- pre-flight connector validation before workflow execution
- dry-run mode + per-node execution records
- execution guard preventing concurrent workflow runs
- task timeouts + on_failure hook for workflow nodes
- NodeExecutionObserver protocol + refactor progress out of execute_node
- sub-operation progress tracking, workflow extraction, and simplify cleanup
- v0.3.3 — dashboard stats, version flow fix, roadmap resequencing
- track library pages, settings redesign, and nested API config
- v0.3.2 — DRY refactoring, checkpoint batching, and docs restructuring
- v0.3.1 — imports & real-time progress in the web UI
- v0.3.0 — web UI foundation, FastAPI backend, and playlist CRUD
- add multi-artist fallback and improve Last.FM logging levels
- improve metric display and update dependencies
- preserve playlist track metadata with PlaylistEntry pattern
- consolidate progress system and enhance Spotify operations
- implement unified progress tracking with Progress.console coordination
- resolve LastFM concurrency bottleneck and DRY code consolidation
- implement modular play import system with connector-specific factories
- implement comprehensive batch processing and error handling infrastructure
- complete Last.fm import system with enhanced track resolution and pagination
- implement unified Spotify import service with enhanced track identity resolution
- complete unambiguous identity pipeline refactor with clean architecture
- add data retrieval use cases and enhance play history management
- implement ultra-DRY enhanced playlist naming with template support
- complete infrastructure and workflow system overhaul
- implement comprehensive play history filtering and database optimization
- complete v0.2.4 playlist workflow expansion with comprehensive refactor
- complete Clean Architecture migration with UnitOfWork pattern and perfect code quality
- fix runtime workflow execution failures and implement comprehensive test improvements
- resolve SQLite database locking issues with session management improvements
- implement comprehensive metadata management and CLI restructuring

### Maintenance

- v0.4.10 cleanup — resolver consolidation, branding, rules refinement
- migrate from Poetry to uv
- bump dev-setup-guide submodule to 61fe1b1
- move completed/ into backlog/, audit dev-setup-guide for clarity
- v0.4.5 — code & test suite hardening
- add claude rules and running log
- reorganize docs and add claude agents
- upgrade to Python 3.14.2 and update all dependencies
- remove 2 unused imports

### Performance

- optimize bulk_upsert and update with identity map pattern

### Refactoring

- split Settings into Integrations + Sync sub-pages with collapsible sidebar nav
- DRY cleanup across repositories and frontend
- simplify v0.4.6 per code review
- extract dev-setup-guide into git submodule
- remove dead code, harden static analysis, and enforce strict track invariants
- dark editorial UI polish, extract SectionHeader, fix decodeHtmlEntities
- atomic sync+upsert in playlist_source connector branch
- restructure modules, remove dead code, and align test paths
- typed connector protocols, per-UoW caching, and codebase cleanup
- relocate modules to correct layers and redesign metrics system
- type architecture cleanup and Pydantic boundary validation
- migrate to native async httpx, fix logging, harden source nodes
- DRY consolidation and web interface readiness
- migrate from backoff to tenacity for retry policies
- configure tooling for Python 3.14 and fix string literal bugs
- modernize to Python 3.14 patterns in src/
- add slots=True to result value objects for memory efficiency
- convert remaining @dataclass to @define for 100% attrs consistency
- replace validate() methods with attrs field validators (Phase 1)
- extract BaseMatchingProvider to eliminate workflow duplication
- extract HTTPErrorClassifier base class to eliminate duplication
- eliminate duplication and modernize use case layer
- modularize domain transforms and eliminate duplication
- complete connector architecture migration and code reorganization
- restructure connector architecture with modular design
- enhance operation tracking and progress monitoring across import services
- modernize track matching system with pluggable provider pattern
- restructure project to clean architecture with src/ layout

### Testing

- add comprehensive test coverage for new matching system

### Cleanup

- remove redundant session regression test and complete Clean Architecture migration
