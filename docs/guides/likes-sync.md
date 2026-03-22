# Mixd Likes Synchronization Guide

This guide explains how to use Mixd to synchronize "liked" tracks between different music services, with Mixd serving as the source of truth for your music preferences.

## Overview

Mixd's likes synchronization system enables you to:

1. **Import liked tracks from Spotify** into the local Mixd database
2. **Export liked tracks from Mixd to Last.fm** as "loved" tracks
3. **Maintain synchronization over time** with incremental updates
4. **Preserve complete track metadata** while handling cross-service differences

The system uses intelligent track matching and checkpoint tracking to ensure reliable, resumable operations that handle the complexities of cross-service synchronization.

## Prerequisites

Before using the likes synchronization features, ensure:

1. **API credentials** configured in `.env` (see [development.md](../development.md) for setup)
2. **Database initialized** — runs automatically on first use
3. **Service connections verified** — run `mixd connectors` to check status

## Command Usage

### Importing Liked Tracks from Spotify

```bash
mixd likes import-spotify [OPTIONS]
```

This command fetches tracks you've saved/liked on Spotify and imports them into the Mixd database, preserving their like status and complete metadata.

#### Options

- `--limit` / `-l` — tracks per API request batch (higher = fewer API calls)
- `--max-imports` / `-m` — maximum total tracks to import (unlimited if not specified)

#### Examples

```bash
# Import all liked tracks from Spotify
mixd likes import-spotify

# Import only the first 500 liked tracks
mixd likes import-spotify --max-imports 500

# Adjust API batch size for slower connections
mixd likes import-spotify --limit 25
```

#### What Happens During Import

1. **Fetch Liked Tracks**: Retrieves your saved tracks from Spotify
2. **Track Resolution**: Creates or updates tracks in the local database
3. **Metadata Storage**: Preserves complete Spotify metadata
4. **Like Status**: Marks tracks as liked in Mixd
5. **Progress Tracking**: Shows real-time progress with Rich formatting
6. **Checkpoint Saving**: Enables resumable operations if interrupted

### Exporting Liked Tracks to Last.fm

```bash
mixd likes export-lastfm [OPTIONS]
```

This command identifies tracks that are liked in Mixd but not yet loved on Last.fm, and marks them as loved on Last.fm through intelligent track matching.

#### Options

- `--batch-size` / `-b` — tracks per API request batch (Last.fm has rate limits)
- `--max-exports` / `-m` — maximum total tracks to export (unlimited if not specified)
- `--date` — override checkpoint date, export tracks liked since this date (ISO format: `2025-08-01`)

#### Examples

```bash
# Export all liked tracks to Last.fm
mixd likes export-lastfm

# Export with smaller batch size for API stability
mixd likes export-lastfm --batch-size 25

# Export only the first 100 tracks for testing
mixd likes export-lastfm --max-exports 100

# Re-export everything since a specific date
mixd likes export-lastfm --date 2025-01-01
```

#### What Happens During Export

1. **Track Matching**: Uses sophisticated algorithms to match tracks between services
2. **Confidence Scoring**: Ensures high-quality matches before export
3. **API Integration**: Marks tracks as "loved" on Last.fm
4. **Progress Tracking**: Shows matching success rates and export progress
5. **Error Handling**: Gracefully handles API limits and failures
6. **Checkpoint Saving**: Tracks export progress for resumability

## Architecture

For details on how the likes sync system is structured (use cases, domain entities, connectors, repositories), see [Architecture: Layers & Patterns](../architecture/layers-and-patterns.md).

## Common Scenarios

### Initial Complete Synchronization

To fully synchronize your likes between Spotify and Last.fm for the first time:

```bash
# Step 1: Import all liked tracks from Spotify
mixd likes import-spotify

# Step 2: Export all liked tracks to Last.fm
mixd likes export-lastfm
```

**Expected Results**:
- All Spotify liked tracks imported to local database
- Tracks successfully matched to Last.fm marked as "loved"
- Progress tracking shows match success rates
- Checkpoint saved for future incremental updates

### Incremental Updates

After the initial synchronization, run the same commands periodically to keep services in sync:

```bash
# Run weekly or monthly to catch new likes
mixd likes import-spotify
mixd likes export-lastfm
```

The checkpoint system ensures only new changes are processed, making subsequent runs much faster.

### Large Library Synchronization

For very large music libraries, use batch sizing and limits:

```bash
# Import with lower API batch size for stability
mixd likes import-spotify --limit 25

# Export in smaller batches with rate limiting consideration
mixd likes export-lastfm --batch-size 25
```

### Testing and Validation

To test the system before full synchronization:

```bash
# Test import with a small subset
mixd likes import-spotify --max-imports 100

# Test export with a small subset
mixd likes export-lastfm --max-exports 50

# Check service connections
mixd connectors
```

## Track Matching System

### How Track Matching Works

Mixd uses a sophisticated multi-stage matching process:

1. **Exact ID Matching**: Uses Spotify ID, ISRC, and MusicBrainz ID for deterministic matches
2. **Metadata Matching**: Fuzzy matching on artist names and track titles
3. **Confidence Scoring**: Each match receives a confidence score (0-100)
4. **Threshold Filtering**: Only high-confidence matches (typically >70) are exported

### Matching Success Rates

Typical matching success rates:
- **Spotify → Last.fm**: 85-95% for mainstream music
- **Older/Obscure Tracks**: 70-85% success rate
- **Classical/Non-English**: Variable, often 60-80%

## Related Documentation

- **[CLI Reference](cli.md)** - Complete CLI command reference
- **[Workflow Guide](workflows.md)** - Workflow system for advanced playlist operations
- **[Architecture](../architecture/README.md)** - System architecture and design patterns
- **[Database](../architecture/database.md)** - Database schema for tracks, likes, and sync checkpoints
- **[Development](../development.md)** - Developer setup and common task recipes
- **[Planning & Backlog](../backlog/README.md)** - Future enhancements planned for likes synchronization
