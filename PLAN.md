# Plan: Implementing Primary Connector Track for Relinking

**Date**: 2024-07-29
**Status**: Not Started

## 1. The "Why": Context and Problem

Currently, our system allows a single canonical track in our database to be associated with multiple `connector_tracks` from the same service (e.g., Spotify). This is expected behavior, especially with services like Spotify that re-release or remaster tracks over time.

The problem is that we lack a formal way to identify which of these multiple `connector_tracks` is the **primary** or **active** one. When we need to perform an action, like adding a track to a Spotify playlist, we must send the single, currently correct Spotify Track ID.

### The Driver: Spotify Track Relinking

This issue is most critical when handling Spotify's track relinking process. As detailed in their documentation, when we request information for an old track ID, the API may respond with data for a *new* track ID, including a `linked_from` field that points back to our original request.

**Spotify Documentation**: Track Relinking Concept

**Example API Response from our `SCRATCHPAD.md`**:

When we request the old track ID `1W2ox3KxfTEQfO5NdOaK7E`, Spotify's API returns data keyed by the **new, current** track ID `1QdVwO5xqAI88pNn2qfNjU`:

```json
{
  "1QdVwO5xqAI88pNn2qfNjU": {
    "id": "1QdVwO5xqAI88pNn2qfNjU",
    "name": "White Sky", 
    "artists": [{"name": "Vampire Weekend"}],
    "linked_from": {
      "id": "1W2ox3KxfTEQfO5NdOaK7E",
      "uri": "spotify:track:1W2ox3KxfTEQfO5NdOaK7E"
    }
  }
}
```

Our system needs to recognize this event and ensure that the mapping for the new ID (`1QdVwO5xqAI88pNn2qfNjU`) is marked as the primary one for all future API calls.

### The Goal: A Generic Solution

While Spotify is the immediate driver, the solution must be generic. It should establish a "primary" mapping concept that works for **any connector**. This future-proofs our architecture and provides a consistent way to get the "best" identifier for any track on any service.

## 2. Proposed Solution

The recommended approach is to add an `is_primary` boolean flag to the `track_mappings` table.

**Why this approach?**
-   **Explicit & Clear**: The primary status is unambiguously defined in the data model. There's no guesswork based on timestamps or other implicit fields.
-   **Robust**: We can enforce the business rule "only one primary mapping per track, per connector" with a database constraint, preventing data corruption.
-   **Correct Modeling**: The "primary" status is a property of the *relationship* between our canonical track and a connector track. The `track_mappings` table represents this relationship, making it the correct place for this flag.

> **Developer Autonomy**: This is the recommended path, but you have the autonomy to explore and propose alternatives if you discover a more elegant or effective solution during implementation. The key requirements are correctness, robustness, and adherence to our Clean Architecture principles.

## 3. Implementation Plan

This plan is broken into three phases to tackle the database, write path, and read path separately.

### Phase 1: Database and Data Model Foundation

The goal here is to update our schema to support the `is_primary` concept.

1.  **Modify `DBTrackMapping` Model**:
    -   **File**: `src/infrastructure/persistence/database/db_models.py`
    -   **Action**: Add a new column `is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)`.

2.  **Create Alembic Migration**:
    -   **Action**: Generate a new Alembic migration script.
    -   **Details**: The script must do two things:
        1.  Add the `is_primary` column to the `track_mappings` table.
        2.  Add a **partial unique index** to enforce the business rule. For SQLite, this looks like: `CREATE UNIQUE INDEX uq_primary_mapping ON track_mappings (track_id, connector_name) WHERE is_primary = TRUE;` (Note: The `connector_name` will need to be joined from `connector_tracks`). A more direct index on `track_mappings` would be on `(track_id, connector_track_id)` but the business rule is per-connector. We may need to adjust the index to best fit the query patterns. Let's start with the conceptual goal: one primary per `track_id` and `connector_name`.

3.  **Backfill Existing Data**:
    -   **Action**: Within the migration, add logic to populate the new flag for existing data.
    -   **Strategy**:
        -   For any `(track_id, connector_name)` pair with only **one** mapping, set `is_primary = True`.
        -   For pairs with **multiple** mappings, a safe default is to mark the **most recently created** mapping as primary. The system will self-correct as it encounters relinking events.

### Phase 2: Updating the Write Path (Handling Relinking)

This phase ensures that when we detect a relinking event, we correctly set the primary flag.

1.  **Centralize the Logic in the Repository**:
    -   **Files**: `src/domain/repositories/interfaces.py`, `src/infrastructure/persistence/repositories/track/connector.py`.
    -   **Action**: Create a new method in `ConnectorRepositoryProtocol` called `set_primary_mapping(track_id: int, connector_track_id: int, connector_name: str)`.
    -   **Implementation**: This method is the heart of the new logic. It must, in a single transaction:
        1.  Find all mappings for the given `track_id` and `connector_name`.
        2.  Set `is_primary = False` for all of them.
        3.  Set `is_primary = True` for the single mapping that matches the provided `connector_track_id`.

2.  **Update the Spotify Play Resolver**:
    -   **File**: `src/infrastructure/services/spotify_play_resolver.py`
    -   **Action**: Modify the `_resolve_direct_with_relinking` method (or similar logic where the API response is processed).
    -   **Logic**: When a `linked_from` field is detected in the Spotify track data, it signifies a relink. After ensuring the new connector track and its mapping exist, call the new `connector_repository.set_primary_mapping()` method to designate the new track's mapping as primary.

### Phase 3: Updating the Read Path (Using the Primary ID)

This phase ensures the rest of the application transparently uses the primary ID.

1.  **Update the Track Mapper**:
    -   **File**: `src/infrastructure/persistence/repositories/track/mapper.py`
    -   **Action**: Modify the `TrackMapper.to_domain` method.
    -   **Logic**: The logic that builds the `connector_track_ids` dictionary for the `Track` domain entity needs to be smarter. It should iterate through the mappings and, if multiple exist for one connector, it must prioritize the one where `is_primary is True`.

2.  **Verify Downstream Consumers (No Code Change Expected)**:
    -   **Files**: `src/infrastructure/connectors/spotify.py` (specifically `_extract_spotify_track_uris`), `src/application/use_cases/update_connector_playlist.py`.
    -   **Action**: Verify that these files work correctly without changes.
    -   **Reasoning**: These components operate on the `Track` domain entity. Since we've corrected how that entity is constructed in the mapper, they will automatically receive the correct primary ID from `track.connector_track_ids`. This is a key benefit of our architecture.

## 4. Files to be Modified

-   `src/infrastructure/persistence/database/db_models.py`
-   A new Alembic migration file in `alembic/versions/`
-   `src/domain/repositories/interfaces.py`
-   `src/infrastructure/persistence/repositories/track/connector.py`
-   `src/infrastructure/services/spotify_play_resolver.py`
-   `src/infrastructure/persistence/repositories/track/mapper.py`

## 5. Testing Strategy

-   **Unit Tests**:
    -   Test the new `set_primary_mapping` repository method to ensure it correctly toggles the flags.
    -   Test the `SpotifyPlayResolver` to confirm it calls `set_primary_mapping` when it processes a relinked track.
    -   Test the `TrackMapper` to ensure it selects the primary mapping when multiple are present.
-   **Integration Tests**:
    -   Create a test that simulates a Spotify import with a relinked track and assert that the `is_primary` flag is correctly set in the database.
    -   Verify that an existing test for creating a Spotify playlist still passes, which implicitly confirms the read path is working correctly.