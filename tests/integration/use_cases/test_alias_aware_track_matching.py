"""End-to-end: cached MusicBrainz aliases rescue a cross-spelling track match.

The epic's story, run against the real alias cache. A scrobble credited to
"TEED" is scored against a library track credited to "Totally Enormous
Extinct Dinosaurs"; with the alias rows seeded the pair reaches the accept
zone, and without them the same pair is refused.
"""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid7

from src.application.use_cases.match_and_identify_tracks import (
    MatchAndIdentifyTracksCommand,
    MatchAndIdentifyTracksUseCase,
)
from src.domain.entities.track import ArtistCredit, Track, TrackList
from src.infrastructure.persistence.repositories.factories import get_unit_of_work
from tests.fixtures import FULL_NAME, TEED, seed_aliases


async def resolve_scrobble(uow, user_id: str) -> bool:
    """Run the use case over one Last.fm-shaped answer. True when accepted."""
    track = Track(
        id=uuid7(),
        title="Garden (Live)",
        user_id=user_id,
        artists=[ArtistCredit(credited_name=FULL_NAME)],
    )
    identity_service = AsyncMock()
    identity_service.get_existing_identity_mappings.return_value = {}
    identity_service.get_raw_external_matches.return_value = {
        track.id: {
            "connector_id": "lastfm-garden",
            "match_method": "artist_title",
            "service_data": {"title": "Garden", "artist": TEED},
        }
    }
    identity_service.persist_identity_mappings.return_value = None
    uow.get_track_identity_service = MagicMock(return_value=identity_service)

    result = await MatchAndIdentifyTracksUseCase().execute(
        MatchAndIdentifyTracksCommand(
            user_id=user_id,
            tracklist=TrackList(tracks=[track]),
            connector="lastfm",
            connector_instance=AsyncMock(),
        ),
        uow,
    )
    return result.resolved_count == 1


class TestAliasAwareTrackMatching:
    async def test_cached_aliases_carry_the_cross_spelling_match(self, db_session):
        uow = get_unit_of_work(db_session)
        await seed_aliases(uow, TEED, FULL_NAME)

        assert await resolve_scrobble(uow, f"alias-match-{uuid7()}")

    async def test_the_same_pair_is_refused_with_an_empty_alias_cache(self, db_session):
        uow = get_unit_of_work(db_session)

        assert not await resolve_scrobble(uow, f"alias-match-{uuid7()}")
