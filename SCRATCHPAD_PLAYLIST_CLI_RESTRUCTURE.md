# 🎯 Active Work Tracker - Playlist CLI Command Restructure

> [!info] Purpose
> This file tracks active development work on the current epic. For strategic roadmap and completed milestones, see [[BACKLOG.md]].

**Current Initiative**: Playlist CLI Command Restructure
**Status**: `#completed` `#interface` `#v1.0`
**Last Updated**: 2025-01-19

## Progress Overview
- [x] **Restructure playlist commands with proper hierarchy** ✅ (Completed)
- [x] **Add playlist data management commands (list/delete)** ✅ (Completed)

---

## ✅ COMPLETED Epic: Playlist CLI Command Restructure `#completed`

**Goal**: Reorganize playlist CLI commands to create clear separation between workflow execution (primary use case) and playlist data management, while adding missing CRUD operations for stored playlists.

**Why**: Current command structure mixes workflow operations with data operations at the same level, creating confusion. Users can't easily distinguish between managing workflow definitions vs managing stored playlist data. Adding playlist listing and deletion completes the CRUD operations needed for playlist management.

**Effort**: S - Command restructuring with some new repository methods and CLI logic

### 🤔 Key Architectural Decision
> [!important] Top-Level Workflow Commands + Human-First Design
> **Key Insight**: After researching September 2025 CLI best practices, workflows are significant enough to be top-level commands (like `git branch`, `docker image`). Current nested approach violates `[tool] [noun] [verb]` conventions and creates unnecessary verbosity for the primary use case.
>
> **Chosen Approach**: Promote workflows to top-level `narada workflow` commands, following established platform patterns. Implement progressive discovery for new users while maintaining automation-friendly direct access for power users.
>
> **Rationale**:
> - **Platform Consistency**: Follows `[tool] [noun] [verb]` pattern used by git, docker, kubectl
> - **Progressive Discovery**: Interactive browser at `narada workflow` guides new users without breaking automation
> - **Human-First Design**: Since primarily human-used, optimize for discoverability and efficiency
> - **Reduced Cognitive Load**: Less typing, clearer semantic separation from playlist data operations

### 📝 Implementation Plan
> [!note]
> Break down the work into logical, sequential tasks.

**Phase 1: Foundation & Modular Architecture**
- [x] **Task 1.1**: Create `workflow_commands.py` module following 2025 Typer patterns ✅
- [x] **Task 1.2**: Add top-level workflow commands to main app with Rich help panel ✅
- [x] **Task 1.3**: Implement progressive discovery (interactive + direct modes) ✅

**Phase 2: Enhanced Workflow Experience**
- [x] **Task 2.1**: Build sophisticated workflow browser with Rich panels and categorization ✅
- [x] **Task 2.2**: Add workflow categorization with smart defaults ✅
- [x] **Task 2.3**: Move existing workflow logic from `playlist_commands.py` to new module ✅

**Phase 3: Playlist Data Management & Repository**
- [x] **Task 3.1**: Add `list_all_playlists()` method to repository interface and implementation ✅
- [x] **Task 3.2**: Clean up `playlist_commands.py` for data operations only (list/delete/backup) ✅
- [x] **Task 3.3**: Implement Rich table formatting and proper DDD use case structure ✅

### ✨ User-Facing Changes & Examples

**New Command Structure:**
```bash
# WORKFLOW EXECUTION (top-level, follows [tool] [noun] [verb] pattern)
narada workflow                       # Interactive workflow browser with progressive discovery
narada workflow list                  # Table of workflow definitions
narada workflow run <id>              # Execute specific workflow

# PLAYLIST DATA MANAGEMENT (clear separation)
narada playlist list                  # Show stored playlists with metadata
narada playlist backup <connector> <id>  # Existing functionality (unchanged)
narada playlist delete <id>           # Remove playlist from local storage
```

**Progressive Discovery Examples:**
```bash
# New user discovery flow
narada workflow                       # Rich interactive browser with categories
narada workflow --help                # Categorized workflows with descriptions
narada workflow run                   # Shows list + prompts if no ID provided

# Power user automation flow
narada workflow run discovery_mix     # Direct execution, no prompts
narada workflow list --format json   # Machine-readable output for scripts
```

**New `playlist list` output:**
```
┏━━━━┳━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━┓
┃ ID ┃ Name           ┃ Description                 ┃ Tracks ┃ Last Updated ┃
┡━━━━╇━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━┩
│ 1  │ Discovery Mix  │ Latest curated tracks       │ 47     │ 2025-01-15   │
│ 2  │ Hidden Gems    │ Underplayed favorites       │ 23     │ 2025-01-12   │
└────┴────────────────┴─────────────────────────────┴────────┴──────────────┘
```

### 🛠️ Implementation Details

**Affected Architectural Layers**:
- **Domain**: Add `list_all_playlists()` to `PlaylistRepositoryProtocol`
- **Application**: No new Use Cases needed (direct repository calls for listing)
- **Infrastructure**: Implement `list_all_playlists()` in `PlaylistRepository` with efficient query
- **Interface**: Create new `workflow_commands.py` module, restructure `playlist_commands.py`, update main app composition

**Testing Strategy**:
- **Unit**: Test new repository method, CLI command parsing and help text
- **Integration**: Test playlist listing with database, delete operations with cascading
- **E2E/Workflow**: Validate full command hierarchy works, workflow execution still functional

**Key Files to Modify**:
- `src/domain/repositories/interfaces.py` - Add list method to protocol
- `src/infrastructure/persistence/repositories/playlist/core.py` - Implement list method
- `src/interface/cli/workflow_commands.py` - NEW: Create workflow command module
- `src/interface/cli/playlist_commands.py` - Clean up for data operations only
- `src/interface/cli/app.py` - Add workflow commands with `add_typer()`
- `tests/unit/interface/cli/test_workflow_commands.py` - NEW: Test workflow commands
- `tests/unit/interface/cli/test_playlist_commands.py` - Test updated playlist commands
- `tests/integration/repositories/test_playlist_repository_integration.py` - Test list method

---

## 🎉 IMPLEMENTATION COMPLETED

### ✅ **What Was Delivered**

1. **Modern CLI Architecture**: Follows 2025 Typer best practices with proper command hierarchy
2. **DDD Compliance**: Proper separation with Interface → Application → Domain → Infrastructure layers
3. **Progressive Discovery**: Interactive workflow browser for new users, direct execution for power users
4. **Rich UI Enhancement**: Beautiful categorized tables, panels, and modern CLI UX patterns
5. **Clean Command Structure**: `narada workflow` for execution, `narada playlist` for data management

### 🚀 **Key Features Implemented**

- **Top-level workflow commands**: `narada workflow`, `narada workflow list`, `narada workflow run`
- **Interactive workflow browser**: Rich categorization (Discovery, Analysis, Organization, Testing)
- **Playlist data management**: `narada playlist list`, `narada playlist delete`, `narada playlist backup`
- **Proper Use Cases**: `ListPlaylistsUseCase` following DDD principles with UnitOfWork pattern
- **Repository enhancements**: `list_all_playlists()` method in domain interface and infrastructure

### 📊 **User Experience Improvements**

- **Clear semantic separation**: Workflows vs stored playlist data
- **Discoverable commands**: Rich help panels with emoji categories
- **Professional CLI**: Consistent with modern tools like git, docker, kubectl
- **Automation-friendly**: Direct command execution while maintaining interactive discovery