# Technology Stack

Core technology decisions, supporting libraries, and architectural benefits that guide Narada's implementation.

## Core Technology Decisions

### Python 3.14+
**Why**: Enhanced typing, performance improvements, modern async patterns
**Usage**: Modern language features throughout codebase, PEP 749 support
**Benefits**: Better type safety, cleaner code, future-proofing, improved asyncio

### SQLite + SQLAlchemy 2.0
**Why**: Zero configuration, atomic transactions, rich relationships
**Usage**: Local database with async ORM patterns and specialized session management
**Benefits**: No server setup, data integrity, complex queries, concurrent operation support

### Prefect 3.0 (Workflow Engine)
**Why**: Modern async workflow orchestration with improved dependency management
**Usage**: Workflow orchestration with embedded mode and built-in dependency injection
**Benefits**: Native async support, retry logic, error handling, real-time feedback, transactional semantics

### Typer + Rich (CLI)
**Why**: Type-safe CLI with beautiful output
**Usage**: Command-line interface with rich formatting
**Benefits**: Auto-completion, validation, professional UX

### attrs (Domain Models)
**Why**: Immutable objects with minimal boilerplate
**Usage**: Domain entities and value objects
**Benefits**: Immutability, type safety, clean constructors

## Supporting Technologies

| Technology | Purpose | Rationale |
|------------|---------|-----------|
| **httpx** | HTTP client for all APIs | Async-first, native OAuth/rate limiting, replaces spotipy/pylast/musicbrainzngs |
| **tenacity** | Retry logic | Declarative retry patterns, exponential backoff, async-native |
| **aiolimiter** | Rate limiting | Async rate limiting for API compliance, leaky bucket algorithm |
| **rapidfuzz** | String matching | High-performance fuzzy matching for track resolution |
| **toolz** | Functional utilities | Functional composition, efficient data processing |
| **loguru** | Logging | Context-aware logging, minimal configuration |

---

## Architectural Benefits

### Current Capabilities
- **Smart Playlist Operations**: Cross-service data transformations
- **Bidirectional Synchronization**: Intelligent track matching between services
- **Comprehensive Data Ownership**: Complete play history and metadata control
- **Sophisticated Updates**: Differential playlist operations with conflict resolution

### Future Extensibility

#### Adding New Music Services

Each music service connector is completely self-contained in its own folder:

```
src/infrastructure/connectors/spotify/
├── models.py              # Pydantic models for API response shapes
├── client.py              # API client (auth, requests, validates → models)
├── connector.py           # Main service interface
├── factory.py             # Creates all Spotify services
├── operations.py          # Core operations (get playlists, etc)
├── matching_provider.py   # Track matching logic
├── conversions.py         # Typed model → Domain model conversion (+ relinking propagation)
├── error_classifier.py    # Service-specific error handling
├── play_importer.py       # Play history import
├── play_resolver.py       # Play record resolution
├── inward_resolver.py     # Spotify ID → canonical track resolution (handles relinking)
├── personal_data.py       # GDPR export parsing
├── playlist_sync_operations.py # Playlist sync logic
└── utilities.py           # Spotify-specific helpers

src/infrastructure/connectors/_shared/
├── error_classifier.py      # ErrorClassifier protocol + HTTPErrorClassifier base
├── failure_handling.py      # Match failure logging and utilities
├── inward_track_resolver.py # Base class for connector ID → canonical track resolution
├── isrc.py                  # Shared ISRC normalization/validation
├── matching_provider.py     # BaseMatchingProvider ABC (template method)
├── metric_registry.py       # Metric resolver registry
├── rate_limited_batch_processor.py
└── retry_policies.py        # Tenacity retry configuration
```

**To add YouTube Music:**
1. Copy existing connector: `cp -r src/infrastructure/connectors/spotify src/infrastructure/connectors/youtube_music`
2. Rename classes: `SpotifyConnector` → `YouTubeMusicConnector`
3. Implement interfaces: `ConnectorProtocol`, `MatchProvider`, etc.
4. Register in connector factory - **done!**

**Benefits**: Self-contained design means zero changes to other services when adding new ones.

#### Other Extensions
- **Web Interface** (v0.3.0): FastAPI backend using `execute_use_case()` runner + React frontend. Interface layer already restructured — CLI-specific code isolated in `interface/cli/`, `application/runner.py` ready for `Depends()` injection. See [`docs/web-ui/`](../web-ui/README.md) for user flows, API contracts, IA, and frontend architecture.
- **Advanced Analytics**: Machine learning on comprehensive listening data
- **Collaborative Features**: Multi-user support with existing architecture

### Technical Scalability
- **Database**: SQLite handles millions of tracks efficiently
- **API Efficiency**: Batch operations and caching minimize external calls
- **Memory Usage**: Streaming operations and lazy loading for large datasets
- **Performance**: Async-first design enables concurrent operations
