
# Project Narada Backlog

**Current Development Version**: 0.2.4
**Current Initiative**: Playlist Workflow Expansion

This document is a high level overview of Project Narada's development backlog/roadmap. It's mean to primarily explain the why, at a product manager level, of our future features. It also includes high level architectural decisions, with a focus on the why of those descriptions.

[[SCRATCHPAD]] - The SRACTHPAD.md file is where full detail of development tasks are tracked, vs BACKLOG.md (this document), which is for strategic and high level architectural roadmap.

## Reference Guide 📋

### Effort Estimates

Never estimate time, always estimate based on relative effort.

| Size    | Complexity Factors           | Criteria                                                                       |
| ------- | ---------------------------- | ------------------------------------------------------------------------------ |
| **XS**  | Well known, isolated         | Minimal unknowns, fits existing components, no dependencies                    |
| **S**   | A little integration         | Simple feature, 1–2 areas touched, low risk, clear requirements                |
| **M**   | Cross-module feature         | 3–4 areas involved, small unknowns, minor dependencies                         |
| **L**   | Architectural impact         | ≥3 subsystems, integrations, external APIs, moderate unknowns                  |
| **XL**  | High unknowns & coordination | Cross-team, backend + frontend + infra, regulatory/security concerns           |
| **XXL** | High risk & exploration      | New platform, performance/security domains, prototype-first, many dependencies |

### Status Options
- 🔜 Not Started
- 🔄 In Progress
- 🛑 Blocked
- ✅ Completed

## Version Change-log 🆕

### v0.2.1: Like Sync
Keep your music preferences synchronized across services - automatically sync your Spotify likes to Last.fm loves, ensuring your musical taste is consistently reflected everywhere you listen.

**New Features**
- **Import Spotify Likes**
  - Import liked tracks from Spotify
- **Export Canonical Likes to Last.fm**
  - Export likes from Narada to Last.fm as loves 

**Updated Features**
- n/a

**Architecture Improvements**
- **Sync Checkpoint System**
  - Database-backed incremental synchronization with resumable operations
  - Tracks last timestamp and cursor position for each service and entity type

### v0.2.2: Play History
Preserve your complete listening journey forever. Import your entire play history from Spotify GDPR exports and Last.fm, creating a comprehensive backup that lets you analyze your musical evolution over time.

**New Features**
- **Spotify Play History Import**
  - Import play history from Spotify GDPR JSON files
- **Last.fm Play History Import**
  - Import play history from Last.fm API
- **Enhanced Track Resolution**
  - Ensures the most recent Spotify ID is mapped during import

**Updated Features**
- n/a

**Architecture Improvements**
- n/a

### v0.2.3: Clean Architecture Foundation  
Rebuilt the entire codebase for reliability and future growth. While invisible to users, this foundation enables faster feature development and prepares Narada for advanced capabilities like web interfaces and complex workflow automation.

**New Features**
- n/a

**Updated Features**
- n/a

**Architecture Improvements**
- **Project Structure Migration**
  - Migrated from legacy `/narada` to modern `/src` structure with consistent import paths
- **Service Layer Reorganization**
  - Moved business logic to application layer, established proper CLI → Application → Domain flow
- **Architecture Compliance & Quality**
  - Verified Clean Architecture principles, updated to Python 3.13 patterns
- **Matcher System Modernization**
  - Transformed 961-line monolithic matcher into modular provider pattern
- **Workflow Node Architecture**
  - Created SavePlaylistUseCase with Command/Strategy patterns, simplified workflow nodes to delegators

### v0.2.4: Playlist Updates 
Transform your playlists with intelligent automation. Create sophisticated workflows that can update existing playlists with smart differential algorithms, preserving your metadata while seamlessly managing even large playlist changes across services.

**New Features**
- **Comprehensive Playlist CRUD Operations**
  - Create, read, update, and delete playlists with full domain modeling
- **Sophisticated Playlist Updates**
  - Differential update algorithms that preserve track metadata and ordering
- **Playlist Diff Engine**
  - Smart comparison system for tracking playlist changes and generating operation lists

**Updated Features**  
- **Workflow Node Orchestration**
  - Simplified workflow nodes to pure orchestrators, delegating business logic to use cases
- **Track Metrics System**
  - Database-first caching strategy with track_metrics table as single source of truth

**Architecture Improvements**
- **Clean Architecture Compliance**
  - Moved CLI from infrastructure to proper interface layer (`src/interface/cli/`)
- **Use Case Consolidation**
  - Centralized all business logic in application layer use cases with proper dependency injection
- **Domain Layer Expansion**
  - Added comprehensive playlist domain entities with immutable operations
- **UnitOfWork Pattern**
  - Consistent transaction boundary management across all playlist operations
---

## Planned Roadmap 🚀

### v0.2.5: Workflow Transformation Expansion
Unlock the power of your listening history for intelligent playlist curation. Filter and sort your music based on actual listening patterns and discovery opportunities to create playlists that truly reflect your musical journey.

- [ ] **Play History Filter and Sort**
    - Status: 🔄 In Progress
    - Effort: M
    - What: Transform workflows to leverage your listening history for intelligent track selection and ordering
    - Why: Create personalized playlists based on how you actually listen - find hidden gems you haven't heard in months or tracks you've been obsessing over
    - Dependencies: n/a
    - Notes:
        - **Play Count Filters**: Find frequently played hits (>10 plays) or neglected gems (<5 plays)
        - **Time-Based Analysis**: Focus on recent activity (last 30 days) or specific time periods
        - **Discovery Tools**: Identify tracks not played recently for rediscovery playlists
        - **Smart Sorting**: Order by play recency, frequency, or discovery gaps

- [ ] **Advanced Transformer Workflow nodes**
    - Status: 🔜 Not Started
    - Effort: M
    - What: Implement additional transformer nodes for workflow system
    - Why: More transformation options enable more powerful workflows
    - Dependencies: None
    - Notes:
        - Implement combining operations with different strategies
        - Add time-based transformers (seasonal, time of day)
        - Include randomization with optional weighting for sorting a playlist
        - Include selection of just the first X or last X from a tracklist

- [ ] **Advanced Track Matching Strategies**
    - Status: 🔜 Not Started
    - Effort: M
    - What: Extend playlist update matching beyond simple Spotify ID matching to include ISRC and metadata strategies
    - Why: Enable more sophisticated track matching for playlist updates, especially useful when Spotify IDs aren't available or when handling cross-service track resolution
    - Dependencies: Complete UpdatePlaylistUseCase Implementation
    - Notes:
        - Implement ISRC-based matching for high-confidence identity resolution
        - Add metadata matching (artist/title/album) with confidence scoring
        - Support fallback strategies when primary matching fails
        - Maintain existing Spotify ID matching as fastest path
        - File: `src/application/use_cases/update_playlist.py` (currently uses simple Spotify ID matching)

- [ ] **Enhanced Playlist Naming**
    - Status: 🔜 Not Started
    - Effort: M
    - What: Add update and parameterization capability to destination nodes that create playlists
    - Why: Enable dynamic playlist naming and descriptions
    - Dependencies: None
    - Notes:
        - Support template parameters in playlist names
        - Allow using source playlist names in new playlist names/descriptions
        - Add the ability to append date/time to names and descriptions
        - Implement metadata insertion into descriptions
        - Add validation to prevent invalid characters
    
- [ ] **Discovery Workflow Templates**
    - Effort: S
    - What: Create pre-built workflow templates leveraging new play history capabilities
    - Why: Reduce complexity for users to access powerful play analysis without workflow construction expertise
    - Dependencies: Play History Filter and Sort Extensions
    - Status: Not Started
    - Notes:
        - Common discovery patterns: "Hidden Gems", "Seasonal Favorites", "Rediscovery", "New vs Old"
        - Templates demonstrate play history node capabilities
        - Provide starting points for user customization

### v0.3.0: Playlist Ownership & Management
**Goal**: Empower users with full ownership and control over their Spotify playlists through a secure, local backup and an intelligent synchronization system.

#### Core Playlist Management Epics
- [ ] **Discover Spotify Playlists**
    - Effort: M
    - What: Add a `narada spotify playlists --list` command to display a user's Spotify playlists, including name, owner (self/other), and track count.
    - Why: Users need a clear inventory before managing playlists. The command provides an organized view to aid selection.
    - CLI Design:
        - Use a tabular format with `rich` for readability (name, owner, tracks).
        - Support filtering by owner (`--self`, `--other`), and sorting (e.g., by name or track count).
        - Web UI Consideration: The command's output format should be easily adaptable to a web-based playlist table.
    - Dependencies: Matcher System Modernization
    - Status: Not Started

- [ ] **Track Spotify Playlists**
    - Effort: M
    - What: Implement `narada spotify playlists --track <playlist_ids>` to select playlists for ongoing management by Narada.
    - Why: Enables granular control, focusing Narada's resources on user-selected collections and triggering an initial backup.
    - CLI Design:
        - Allow tracking multiple playlists at once using Spotify playlist IDs.
        - Provide clear feedback on success, including the number of tracks backed up for each playlist.
        - Handle errors gracefully (e.g., invalid IDs, network issues).
        - Web UI Consideration: This command's logic will translate to a "Track" button or checkbox in the web UI.
    - Dependencies: Discover Spotify Playlists
    - Status: Not Started

- [ ] **Efficiently Sync Tracked Playlists**
    - Effort: L
    - What: Create `narada sync spotify-playlists` to efficiently update "tracked" playlists with changes from Spotify using the `snapshot_id`.
    - Why: Maintains up-to-date backups, protecting against data loss and powering downstream workflows. Efficiency is key for user experience.
    - CLI Design:
        - Use `snapshot_id` to minimize API calls: compare local and remote IDs, fetching full track lists only when necessary.
        - Provide concise feedback: `"Synced <playlist_name> (<changes>), ..."`.
        - Implement a `--force` option to bypass `snapshot_id` check and force a full refresh.
        - Web UI Consideration: This sync operation could be triggered by a "Sync Now" button in the web UI, or run periodically in the background.
    - Dependencies: Track Spotify Playlists
    - Status: Not Started

#### Future Enhancement Epics (Consider for later milestones)
- [ ] **Two-Way Playlist Sync (Advanced)**
    - Effort: XL
    - What: Explore and design a system for true two-way synchronization of playlists, handling potential conflicts between local and Spotify versions.
    - Why: A highly requested feature, but complex due to conflict resolution challenges.
    - Notes: This would involve careful design decisions around conflict resolution strategies (e.g., last-write-wins, manual override). Consider this a significant undertaking for a later milestone (e.g., v0.6.0 or later).

- [ ] **Playlist Diffing and Merging (Advanced)**
    - Effort: L
    - What: Develop tools to visualize differences between local and Spotify playlists and provide options for merging changes selectively.
    - Why: Empowers users to manage complex playlist evolution scenarios.
    - Notes: This could be a valuable addition in conjunction with a two-way sync system or as a standalone feature.

### v0.3.1: User Experience and Reliability
**Goal**: Polish the user experience and improve system reliability

#### Enhanced CLI Experience Epic

- [ ] **Shell Completion Support**
    - Effort: S
    - What: Add shell completion for bash/zsh/fish
    - Why: Improves CLI usability and discoverability
    - Dependencies: None
    - Status: Not Started
    - Notes:
        - Use Typer's built-in completion support
        - Generate completion scripts for major shells
        - Include dynamic completion for workflows and connectors

- [ ] **Progress Reporting Consistency**
    - Effort: S
    - What: Standardize progress reporting across all long-running operations
    - Why: Users need consistent feedback on operation status
    - Dependencies: None
    - Status: Not Started
    - Notes:
        - Use unified progress provider interface
        - Add ETA calculations where possible
        - Include operation-specific progress details

---

### v0.4.0: Core Functionality Improvements
**Goal**: Essential functionality improvements based on user feedback

#### Enhanced Capabilities Epics
- [ ] **Manual Entity Resolution Override**
    - Effort: M
    - What: Add ability for users to manually create and edit track matches
    - Why: Automatic matching can't handle all edge cases; manual override needed
    - Dependencies: None
    - Status: Not Started
    - Notes:
        - Allow setting track ID, connector, and connector ID
        - Set confidence to 100% for manual matches
        - Support overriding existing matches
        - Add CLI command for manual match creation
        - Save metadata about who created the match and when

- [ ] **Matcher Status Feedback**
    - Effort: S
    - What: Implement better progress reporting for matcher operations
    - Why: Matching is a long-running process with no visibility
    - Dependencies: None
    - Status: Not Started
    - Notes:
        - Add progress indicators for batch operations
        - Show success/failure counts in real-time
        - Implement optional verbose mode for detailed progress
        - Report service-specific rate limiting information
        - Include estimated completion time

---

### v0.5.0: API-First Interface with Workflow Visualization  
**Goal**: Transform Narada into a service-oriented platform with elegant workflow visualization

#### Modern Web Interface Foundation Epics
- [ ] **FastAPI Service Implementation**
    - Effort: M
    - What: Create FastAPI service exposing core workflow operations
    - Why: Need programmatic access to workflow capabilities before building visualization
    - Dependencies: None
    - Status: Not Started
    - Notes:
        - Key Features:
            - Full Pydantic schema validation
            - Proper error handling with consistent responses
            - Automatic OpenAPI documentation
            - Async support throughout
            - Clear domain boundaries

- [ ] **Workflow Schema Enhancer**
    - Effort: S
    - What: Create adapter that transforms workflow definitions into visualization-friendly schema
    - Why: Current task-based schema lacks presentation metadata for visualization
    - Dependencies: FastAPI Service
    - Status: Not Started
    - Notes:
        - Convert from task/upstream format to nodes/edges model
        - Add position information for layout
        - Include visual metadata (colors, icons, categories)
        - Preserve backward compatibility

- [ ] **DAG Layout Engine**
    - Effort: M
    - What: Implement server-side layout calculation for workflow visualization
    - Why: Need automatic positioning of nodes for clear visualization
    - Dependencies: Workflow Schema Enhancer
    - Status: Not Started
    - Notes:
        - Integrate dagre for hierarchical layout
        - Calculate optimal node positions
        - Respect node categories and relationships
        - Handle large workflows efficiently
        - Cache layout results

- [ ] **React Flow Visualization Component**
    - Effort: M
    - What: Create React visualization component using React Flow
    - Why: Need clean, interactive workflow visualization with minimal code
    - Dependencies: DAG Layout Engine
    - Status: Not Started
    - Notes:
        - Implement read-only visualization first
        - Add node type-based styling
        - Include smooth animations
        - Support zooming and panning
        - Show node details on selection

- [ ] **React App Shell**
    - Effort: S
    - What: Create minimal React application shell around visualization component
    - Why: Need container to host visualization while maintaining minimal footprint
    - Dependencies: React Flow Visualization
    - Status: Not Started
    - Notes:
        - Implement workflow selector
        - Add basic navigation
        - Include error handling
        - Support responsive layout
        - Maintain minimal bundle size

#### API Testing & Quality Assurance
- [ ] **API Test Suite**
    - Effort: S
    - What: Implement comprehensive test suite for API endpoints
    - Why: Need to validate API behavior and prevent regressions
    - Dependencies: FastAPI Service
    - Status: Not Started
    - Notes:
        - Test all core operations
        - Include error cases
        - Validate response formats
        - Test persistence operations
        - Add performance benchmarks

- [ ] **Visualization Test Suite**
    - Effort: S
    - What: Create tests for visualization component and layout engine
    - Why: Need to ensure visualization accurately represents workflows
    - Dependencies: React Flow Visualization
    - Status: Not Started
    - Notes:
        - Test node/edge rendering
        - Validate layout algorithm
        - Test user interactions
        - Include accessibility testing
        - Ensure cross-browser compatibility

---

### v0.6.0: Interactive Workflow Editor
**Goal**: Full editing capabilities with intuitive graphical interface

#### Interactive Editing System Epics
- [ ] **Drag-and-Drop Node Creation**
    - Effort: M
    - What: Implement drag-and-drop node creation from node palette
    - Why: Need intuitive workflow creation experience
    - Dependencies: fastapi
    - Status: Not Started
    - Notes:
        - Create node palette component
        - Implement drag source for node types
        - Add drop target handling
        - Include node positioning logic
        - Support undo/redo

- [ ] **Node Configuration Panel**
    - Effort: L
    - What: Create dynamic configuration panel for node parameters
    - Why: Users need to configure node behavior without JSON editing
    - Dependencies: fastapi
    - Status: Not Started
    - Notes:
        - Generate form from node schema
        - Implement validation
        - Add help text and documentation
        - Support complex parameter types
        - Include preset configurations

- [ ] **Edge Management**
    - Effort: M
    - What: Implement interactive edge creation and deletion
    - Why: Users need to visually connect nodes
    - Dependencies: Drag-and-Drop Node Creation
    - Status: Not Started
    - Notes:
        - Add interactive connection points
        - Implement edge validation
        - Support edge deletion
        - Include edge styling
        - Handle edge repositioning

- [ ] **Workflow Persistence**
    - Effort: S
    - What: Add save/load functionality for workflows
    - Why: Users need to persist their work
    - Dependencies: Node Configuration Panel
    - Status: Not Started
    - Notes:
        - Implement save API endpoint
        - Add version control
        - Support auto-save
        - Include export/import
        - Handle validation during save

- [ ] **In-Editor Validation**
    - Effort: M
    - What: Add real-time validation of workflow structure
    - Why: Users need immediate feedback on workflow validity
    - Dependencies: Edge Management
    - Status: Not Started
    - Notes:
        - Validate node configurations
        - Check edge validity
        - Highlight errors
        - Provide guidance
        - Support auto-correction

---

### v0.7.0: LLM-Assisted Workflow Creation
**Goal**: Natural language workflow creation with LLM integration

#### AI-Powered Creation Epics
- [ ] **LLM Integration Endpoint**
    - Effort: M
    - What: Create API endpoint for LLM-assisted workflow generation
    - Why: Foundation for natural language workflow creation
    - Dependencies: fastapi implementaiton
    - Status: Not Started
    - Notes:
        - Implement secure LLM API wrapper
        - Add prompt engineering system
        - Support conversation context
        - Include result validation
        - Handle rate limiting

- [ ] **Workflow Generation from Text**
    - Effort: L
    - What: Implement system to translate natural language to workflow definitions
    - Why: Enable non-technical users to create workflows
    - Dependencies: LLM Integration Endpoint
    - Status: Not Started
    - Notes:
        - Design specialized prompts
        - Implement node mapping
        - Add configuration extraction
        - Include workflow validation
        - Support complex workflow patterns

- [ ] **Visualization Confirmation UI**
    - Effort: M
    - What: Create interface for reviewing and confirming LLM-generated workflows
    - Why: Users need to verify generated workflows before saving
    - Dependencies: Workflow Generation from Text
    - Status: Not Started
    - Notes:
        - Show visualization of generated workflow
        - Highlight key components
        - Allow immediate adjustments
        - Provide explanation of structure
        - Include confidence indicators

- [ ] **Conversation Interface**
    - Effort: L
    - What: Implement chat-style interface for workflow creation and refinement
    - Why: Natural conversation provides better user experience
    - Dependencies: Visualization Confirmation UI
    - Status: Not Started
    - Notes:
        - Create chat UI component
        - Implement conversation history
        - Add contextual suggestions
        - Support workflow references
        - Include guided assistance

- [ ] **LLM Feedback Loop**
    - Effort: M
    - What: Create system for user feedback on LLM-generated workflows
    - Why: Improve generation quality through user input
    - Dependencies: Conversation Interface
    - Status: Not Started
    - Notes:
        - Implement feedback collection
        - Add result quality tracking
        - Create feedback insights dashboard
        - Support model improvement
        - Include A/B testing

---

### v1.0.0: Production-Ready Workflow Platform
**Goal**: Transform into production-ready platform with robust user management

#### Production Infrastructure Epics
- [ ] **User Authentication System**
    - Effort: M
    - What: Implement secure authentication with JWT and role-based access
    - Why: Need proper user management for multi-user support
    - Dependencies: tbd
    - Status: Not Started
    - Notes:
        - Add JWT authentication
        - Implement role-based access control
        - Support email verification
        - Include password reset
        - Add session management

- [ ] **Workflow Version Control**
    - Effort: L
    - What: Implement version tracking and management for workflows
    - Why: Users need to track changes and revert when needed
    - Dependencies: Team Collaboration Features
    - Status: Not Started
    - Notes:
        - Add versioning system
        - Implement diff visualization
        - Support rollback
        - Include branching
        - Add merge capabilities

- [ ] **Production Monitoring System**
    - Effort: M
    - What: Create comprehensive monitoring and observability
    - Why: Need visibility into system performance and usage
    - Dependencies: v0.7.0
    - Status: Not Started
    - Notes:
        - Implement structured logging
        - Add performance metrics
        - Create monitoring dashboard
        - Include alerting system
        - Support distributed tracing

- [ ] **Workflow Execution Dashboard**
    - Effort: L
    - What: Build visual dashboard for workflow execution monitoring
    - Why: Users need visibility into running workflows
    - Dependencies: Production Monitoring System
    - Status: Not Started
    - Notes:
        - Create real-time execution visualization
        - Add performance metrics
        - Implement log viewer
        - Support debugging tools
        - Include execution history

---

## Future Considerations 💭

### Quality of Life Improvement Epics
- [ ] **Background Sync Capabilities**
    - Effort: M
    - What: Enable scheduled background synchronization of play history and likes
    - Why: Keeps data current without manual intervention
    - Dependencies: Advanced Play Analytics
    - Status: Not Started
    - Notes:
        - Add scheduling system for regular sync jobs
        - Implement incremental sync for efficiency
        - Add configuration for sync frequency and scope
        - Include sync status monitoring

- [ ] **Two-Way Like Synchronization**
    - Effort: M
    - What: Implement bidirectional like synchronization between services
    - Why: Currently only supports one-way sync (Spotify → Narada → Last.fm)
    - Dependencies: None
    - Status: Not Started
    - Notes:
        - Add conflict detection and resolution
        - Implement service prioritization
        - Support timestamp-based resolution
        - Add manual override options
        - Include detailed sync reporting

- [ ] **Advanced Node Palette**
    - Effort: M
    - What: Enhanced node selection interface with categories, search, and favorites
    - Why: Improve workflow creation experience with better node discovery
    - Notes: Good quality-of-life improvement

- [ ] **Workflow Templates**
    - Effort: M
    - What: Pre-built workflow templates for common scenarios
    - Why: Accelerate workflow creation and establish best practices
    - Notes: Could significantly improve onboarding

- [ ] **Workflow Debugging Tools**
    - Effort: L
    - What: Interactive debugging tools for workflow testing
    - Why: Help users identify and fix workflow issues
    - Notes: Important for complex workflow development


### Lower Priority Ideas
- **Advanced Analytics Dashboard**
    - What: Detailed analytics on workflow usage and performance
    - Why: Nice feature but not core to workflow management
    - Notes: Consider after more stable usage patterns emerge

- **Multi-Language Support**
    - What: UI translations for international users
    - Why: Not critical for initial target audience
    - Notes: Revisit based on user demographics

---

## Deferred Clean Architecture Improvements

### Future 0.2.x Development Items (Deferred)
- [ ] **Domain Layer Logging Abstraction**
    - Effort: S
    - What: Create domain logging interface to remove infrastructure dependency from domain layer
    - Why: Current domain layer imports infrastructure logging, violating Clean Architecture
    - Dependencies: None
    - Status: Deferred
    - Notes:
        - Create `src/domain/interfaces/logger.py` protocol
        - Update `src/domain/transforms/core.py` to use dependency injection
        - Create infrastructure logger adapter
        - Not critical for current functionality, defer to focus on performance issues

---
