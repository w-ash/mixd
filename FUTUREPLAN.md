# Future Plan: Manual Mapping and Data Integrity Tools

**Status**: Not Started
**Depends On**: Implementation of Primary Connector Track (`PLAN.md`)

## 1. The "Why": Context and Problem

### The Challenge of Cross-Service Track Mapping

Music services like Spotify and Last.fm have fundamentally different approaches to cataloging tracks, leading to inevitable mapping imperfections that require user intervention:

**Service Disagreements on Track Identity**:
- **Different Releases**: Spotify may separate "Song Title" and "Song Title (Remastered)" while Last.fm treats them as one
- **Artist Variations**: Services disagree on "Artist" vs "Artist feat. Someone" vs "Artist & Someone"
- **Regional Differences**: Same song may have different track IDs across markets/regions
- **Catalog Changes**: Services periodically reorganize their catalogs, creating new IDs for existing content

**Confidence Scoring Limitations**:
- **Low Confidence Matches**: Automated matching may produce 60-80% confidence scores that could be wrong
- **False Positives**: High confidence doesn't guarantee the match represents the user's preferred version
- **Edge Cases**: Unusual characters, multiple artists, or inconsistent metadata can fool automated systems
- **User Preference**: Users may prefer explicit over clean versions, original over remastered, etc.

**Examples Requiring Manual Intervention**:

1. **Same Song, Different Cataloging**:
   - Spotify: `spotify:track:abc123` → "Bohemian Rhapsody" 
   - Last.fm: Track URL points to "Bohemian Rhapsody (2011 Remaster)"
   - User knows: These are the same canonical song

2. **Low Confidence Match**:
   - System matches with 65% confidence due to slight artist name differences
   - User needs to verify: Is this actually the same track or genuinely different?

3. **Wrong Version Preference**:
   - System picks clean version as primary, but user prefers explicit version
   - Multiple remastered editions exist, user wants specific year as canonical

### Why Manual Tools Are Essential

**User Authority**: Users are the ultimate authority on their music library organization and know their preferences better than any algorithm.

**Data Quality Control**: Provides escape hatch when automated systems fail, ensuring long-term data integrity.

**Historical Cleanup**: Enables fixing data quality issues that accumulated over time from various sources and system changes.

## 2. Implementation Plan

This phase provides users with comprehensive tools to manage cross-service track mapping and data quality.

### Core Data Quality Tools

1. **Manual Track Mapping**:
   - **Use Case**: `ManualMapTrackUseCase` 
   - **Command**: `ManualMapTrackCommand` with `track_id`, `connector_name`, `connector_id`, `set_as_primary`
   - **CLI**: `narada tracks map --track-id <id> --connector <name> --connector-id <external_id> [--set-primary]`
   - **Purpose**: Override automated mappings when user knows better

2. **Duplicate Canonical Track Detection**:
   - **CLI**: `narada tracks find-duplicates [--min-similarity <threshold>]`
   - **Logic**: Find canonical tracks with very similar title/artist combinations
   - **Output**: List potential duplicates with similarity scores for user review
   - **Purpose**: Identify tracks that should be merged into single canonical track

3. **Canonical Track Merging**:
   - **Use Case**: `MergeCanonicalTracksUseCase`
   - **CLI**: `narada tracks merge --source-track <id> --target-track <id> [--confirm]`
   - **Logic**: Move all connector mappings and plays from source track to target track, then delete source
   - **Purpose**: Consolidate duplicate canonical tracks into single authoritative record

### Mapping Quality Review Tools

4. **Low Confidence Match Review**:
   - **CLI**: `narada tracks review-confidence [--max-confidence <threshold>] [--connector <name>]`
   - **Logic**: Show mappings below confidence threshold for user verification
   - **Output**: Display track details, connector info, confidence score, and mapping evidence
   - **Purpose**: Help users identify and fix incorrect automated matches

5. **Mapping Status Overview**:
   - **CLI**: `narada tracks review-mappings [--connector <name>] [--show-confidence]`
   - **Logic**: Enhanced version of existing command showing confidence scores and multiple mappings
   - **Output**: List canonical tracks with multiple connector mappings, confidence scores, primary status
   - **Purpose**: Comprehensive view of track mapping quality

### User Experience Enhancements

6. **Interactive Mapping Wizard**:
   - **CLI**: `narada tracks interactive-review`
   - **Logic**: Step through low-confidence matches interactively, allowing user to approve/reject/modify
   - **Features**: Show track metadata, play samples if available, easy approve/reject workflow
   - **Purpose**: Streamlined bulk review process for data cleanup

7. **Mapping Statistics Dashboard**:
   - **CLI**: `narada tracks stats [--connector <name>]`
   - **Output**: Overall mapping quality metrics (confidence distribution, unmapped tracks, duplicates, etc.)
   - **Purpose**: Give users visibility into their data quality status

### Implementation Priority

**Phase 1** (Essential):
- Manual track mapping (#1)
- Duplicate detection (#2) 
- Track merging (#3)

**Phase 2** (Quality of Life):
- Low confidence review (#4)
- Enhanced mapping overview (#5)

**Phase 3** (Advanced):
- Interactive wizard (#6)
- Statistics dashboard (#7)