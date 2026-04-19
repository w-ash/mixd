"""Apply playlist metadata mappings — the v0.7.4 engine.

For each active ``PlaylistMetadataMapping`` owned by the user, this use
case walks the cached ``DBConnectorPlaylist``'s track list, resolves
each connector track to a canonical ``Track``, and applies the mapping's
action:

- ``set_preference``: upsert ``TrackPreference`` with
  ``source="playlist_mapping"`` and ``preferred_at`` from the Spotify
  ``added_at`` (so temporal history is preserved).
- ``add_tag``: add the tag with ``source="playlist_mapping"`` and
  ``tagged_at`` from the Spotify ``added_at``. Idempotent — existing
  tags from any source are silently skipped.

Membership snapshots (``PlaylistMappingMember``) are replaced on every
run. The diff ``prior_members - current_members`` gives us the tracks
that dropped out of the playlist; we clear **only the mapping-sourced**
metadata for those tracks (preferences/tags with ``source="manual"``
are never touched) via the source-filtered
``remove_preferences`` / ``remove_tags`` repository methods.

The caller is responsible for refreshing the ``DBConnectorPlaylist``
cache first — typically via ``RefreshConnectorPlaylistsUseCase``. This
use case reads whatever is in the cache.

Source priority: ``resolve_preference_change`` + ``should_override``
already encode the rule that ``manual`` outranks ``playlist_mapping``
outranks ``service_import``. When multiple mappings on the same
ConnectorPlaylist target the same track with contradictory preferences,
the **stronger preference state** wins (star > yah > hmm > nah) and a
warning is logged. Conflict handling is log-only for v1 — surfacing it
to the UI is a v0.7.4 follow-up (backlog open question #1).
"""

from datetime import UTC, datetime
from uuid import UUID

from attrs import define

from src.config import get_logger
from src.domain.entities.playlist import SPOTIFY_CONNECTOR
from src.domain.entities.playlist_metadata_mapping import (
    PlaylistMappingMember,
    PlaylistMetadataMapping,
)
from src.domain.entities.preference import (
    PREFERENCE_ORDER,
    PreferenceEvent,
    PreferenceState,
    TrackPreference,
    resolve_preference_change,
)
from src.domain.entities.tag import TagEvent, TrackTag
from src.domain.repositories import UnitOfWorkProtocol

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class ImportPlaylistMetadataCommand:
    user_id: str
    connector_name: str = SPOTIFY_CONNECTOR


@define(frozen=True, slots=True)
class ImportPlaylistMetadataResult:
    """Per-action counters grouped by disposition.

    ``preferences_applied`` / ``tags_applied`` count new or upgraded
    rows. ``preferences_cleared`` / ``tags_cleared`` count mapping-
    sourced rows removed because their track dropped out of the
    playlist. ``conflicts_logged`` is the per-track contradiction count
    within this run.
    """

    preferences_applied: int
    preferences_cleared: int
    tags_applied: int
    tags_cleared: int
    conflicts_logged: int
    mappings_processed: int


def _parse_added_at(raw: str | None) -> datetime:
    """Parse Spotify's ISO ``added_at`` or fall back to now(UTC).

    Spotify occasionally returns null for very old playlist rows;
    the fallback keeps the import moving rather than dropping tracks.
    """
    if raw is None:
        return datetime.now(UTC)
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return datetime.now(UTC)


@define(slots=True)
class ImportPlaylistMetadataUseCase:
    async def execute(
        self,
        command: ImportPlaylistMetadataCommand,
        uow: UnitOfWorkProtocol,
    ) -> ImportPlaylistMetadataResult:
        async with uow:
            mapping_repo = uow.get_playlist_metadata_mapping_repository()
            cp_repo = uow.get_connector_playlist_repository()
            connector_repo = uow.get_connector_repository()
            pref_repo = uow.get_preference_repository()
            tag_repo = uow.get_tag_repository()

            mappings = await mapping_repo.list_for_user(user_id=command.user_id)
            if not mappings:
                return ImportPlaylistMetadataResult(0, 0, 0, 0, 0, 0)

            # Index: connector_playlist_id → [mappings]
            mappings_by_cp: dict[UUID, list[PlaylistMetadataMapping]] = {}
            for m in mappings:
                mappings_by_cp.setdefault(m.connector_playlist_id, []).append(m)

            cached_cps = await cp_repo.list_by_connector(command.connector_name)
            cached_by_db_id = {cp.id: cp for cp in cached_cps}

            # Resolve all (connector, track_identifier) pairs in one query.
            all_connections: list[tuple[str, str]] = []
            for cp_id in mappings_by_cp:
                cp = cached_by_db_id.get(cp_id)
                if cp is None:
                    continue
                all_connections.extend(
                    (command.connector_name, item.connector_track_identifier)
                    for item in cp.items
                )
            track_by_key = (
                await connector_repo.find_tracks_by_connectors(
                    all_connections, user_id=command.user_id
                )
                if all_connections
                else {}
            )

            # Build desired state across all mappings, aggregating conflicts.
            desired_preferences: dict[UUID, tuple[PreferenceState, datetime]] = {}
            desired_tags: dict[UUID, dict[str, datetime]] = {}
            current_members_by_mapping: dict[UUID, list[PlaylistMappingMember]] = {}
            conflicts_logged = 0
            now = datetime.now(UTC)

            for cp_id, cp_mappings in mappings_by_cp.items():
                cp = cached_by_db_id.get(cp_id)
                if cp is None:
                    logger.warning(
                        "Skipping mapping: cached ConnectorPlaylist not found",
                        connector_playlist_id=cp_id,
                    )
                    continue

                for mapping in cp_mappings:
                    members: list[PlaylistMappingMember] = []
                    for item in cp.items:
                        track = track_by_key.get((
                            command.connector_name,
                            item.connector_track_identifier,
                        ))
                        if track is None:
                            continue

                        added_at = _parse_added_at(item.added_at)
                        members.append(
                            PlaylistMappingMember(
                                user_id=command.user_id,
                                mapping_id=mapping.id,
                                track_id=track.id,
                                synced_at=now,
                            )
                        )

                        if mapping.action_type == "set_preference":
                            state: PreferenceState = mapping.action_value  # type: ignore[assignment]
                            prior = desired_preferences.get(track.id)
                            if prior is None:
                                desired_preferences[track.id] = (state, added_at)
                            elif prior[0] != state:
                                conflicts_logged += 1
                                logger.warning(
                                    "Conflicting preference mappings for track",
                                    track_id=track.id,
                                    existing=prior[0],
                                    incoming=state,
                                    connector_playlist_id=cp_id,
                                )
                                # Stronger preference wins; tiebreak on
                                # later added_at.
                                if PREFERENCE_ORDER[state] > PREFERENCE_ORDER[prior[0]]:
                                    desired_preferences[track.id] = (
                                        state,
                                        added_at,
                                    )
                        elif mapping.action_type == "add_tag":
                            tags_for_track = desired_tags.setdefault(track.id, {})
                            # First-write-wins on added_at per (track, tag).
                            tags_for_track.setdefault(mapping.action_value, added_at)

                    current_members_by_mapping[mapping.id] = members

            # Apply preferences (batched).
            preferences_applied = 0
            if desired_preferences:
                existing_prefs = await pref_repo.get_preferences(
                    list(desired_preferences), user_id=command.user_id
                )
                prefs_to_write: list[TrackPreference] = []
                pref_events: list[PreferenceEvent] = []
                for track_id, (state, preferred_at) in desired_preferences.items():
                    existing = existing_prefs.get(track_id)
                    if not resolve_preference_change(
                        existing, state, "playlist_mapping"
                    ):
                        continue
                    prefs_to_write.append(
                        TrackPreference(
                            user_id=command.user_id,
                            track_id=track_id,
                            state=state,
                            source="playlist_mapping",
                            preferred_at=preferred_at,
                        )
                    )
                    pref_events.append(
                        PreferenceEvent(
                            user_id=command.user_id,
                            track_id=track_id,
                            old_state=existing.state if existing else None,
                            new_state=state,
                            source="playlist_mapping",
                            preferred_at=preferred_at,
                        )
                    )
                if prefs_to_write:
                    await pref_repo.set_preferences(
                        prefs_to_write, user_id=command.user_id
                    )
                    await pref_repo.add_events(pref_events, user_id=command.user_id)
                preferences_applied = len(prefs_to_write)

            # Apply tags (batched).
            tags_to_write: list[TrackTag] = []
            for track_id, tag_map in desired_tags.items():
                for tag_value, tagged_at in tag_map.items():
                    tags_to_write.append(
                        TrackTag.create(
                            user_id=command.user_id,
                            track_id=track_id,
                            raw_tag=tag_value,
                            tagged_at=tagged_at,
                            source="playlist_mapping",
                        )
                    )
            tags_applied = 0
            if tags_to_write:
                created_tags = await tag_repo.add_tags(
                    tags_to_write, user_id=command.user_id
                )
                tags_applied = len(created_tags)
                if created_tags:
                    await tag_repo.add_events(
                        [
                            TagEvent(
                                user_id=command.user_id,
                                track_id=t.track_id,
                                tag=t.tag,
                                action="add",
                                source="playlist_mapping",
                                tagged_at=t.tagged_at,
                            )
                            for t in created_tags
                        ],
                        user_id=command.user_id,
                    )

            # Diff members + clear mapping-sourced metadata for dropped tracks.
            preferences_cleared = 0
            tags_cleared = 0
            for mapping in mappings:
                current = current_members_by_mapping.get(mapping.id, [])
                current_track_ids = {m.track_id for m in current}

                prior = await mapping_repo.get_members(
                    mapping.id, user_id=command.user_id
                )
                prior_track_ids = {m.track_id for m in prior}
                removed = list(prior_track_ids - current_track_ids)

                if removed:
                    if mapping.action_type == "set_preference":
                        cleared = await pref_repo.remove_preferences(
                            removed,
                            user_id=command.user_id,
                            source="playlist_mapping",
                        )
                        preferences_cleared += cleared
                    elif mapping.action_type == "add_tag":
                        pairs = [(tid, mapping.action_value) for tid in removed]
                        cleared_pairs = await tag_repo.remove_tags(
                            pairs,
                            user_id=command.user_id,
                            source="playlist_mapping",
                        )
                        tags_cleared += len(cleared_pairs)

                await mapping_repo.replace_members(
                    mapping.id, current, user_id=command.user_id
                )

            await uow.commit()

            logger.info(
                "Playlist metadata import complete",
                mappings=len(mappings),
                preferences_applied=preferences_applied,
                preferences_cleared=preferences_cleared,
                tags_applied=tags_applied,
                tags_cleared=tags_cleared,
                conflicts=conflicts_logged,
            )

            return ImportPlaylistMetadataResult(
                preferences_applied=preferences_applied,
                preferences_cleared=preferences_cleared,
                tags_applied=tags_applied,
                tags_cleared=tags_cleared,
                conflicts_logged=conflicts_logged,
                mappings_processed=len(mappings),
            )


async def run_import_playlist_metadata(
    user_id: str,
    connector_name: str = SPOTIFY_CONNECTOR,
) -> ImportPlaylistMetadataResult:
    """Convenience wrapper for route and CLI handlers."""
    from src.application.runner import execute_use_case

    command = ImportPlaylistMetadataCommand(
        user_id=user_id, connector_name=connector_name
    )
    return await execute_use_case(
        lambda uow: ImportPlaylistMetadataUseCase().execute(command, uow),
        user_id=user_id,
    )
