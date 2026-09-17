"""``TrackResolutionService.ingest``: the step order, with zero I/O.

The planner's arms are tested in the domain; here the question is what the
service does with each outcome — which seams it calls, in what order, and
with what — against ``make_mock_uow``.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

from src.application.services.track_resolution import TrackResolutionService
from src.config import create_matching_config
from src.domain.entities import Artist, ConnectorTrack, Track
from src.domain.repositories.connector import ConnectorMappingSpec
from tests.fixtures import TEST_USER_ID, make_mock_uow, make_track

CONNECTOR = "spotify"
ISRC = "GBEXH1900012"


def _payload(
    identifier: str,
    *,
    title: str = "Ibrik",
    artist: str = "Bonobo",
    duration_ms: int | None = 245_733,
    isrc: str | None = None,
) -> ConnectorTrack:
    return ConnectorTrack(
        connector_name=CONNECTOR,
        connector_track_identifier=identifier,
        title=title,
        artists=[Artist(name=artist)],
        duration_ms=duration_ms,
        isrc=isrc,
        raw_metadata={"popularity": 40},
        last_updated=datetime.now(UTC),
    )


def _uow(
    *,
    existing: dict[tuple[str, str], Track] | None = None,
    isrc_owners: dict[str, Track] | None = None,
    name_owners: dict[tuple[str, str], Track] | None = None,
):
    """A UoW whose repositories answer the probes and echo the writes.

    ``save_tracks`` returns its input stamped ``version=1``;
    ``map_tracks_to_connectors`` returns each spec's track carrying the
    spec's connector id, as the real seam does.
    """
    uow = make_mock_uow()
    track_repo = uow.get_track_repository()
    connector_repo = uow.get_connector_repository()

    track_repo.acquire_ingest_lock = AsyncMock(return_value=None)
    track_repo.find_tracks_by_isrcs = AsyncMock(return_value=isrc_owners or {})
    track_repo.find_tracks_by_title_artist = AsyncMock(return_value=name_owners or {})
    track_repo.save_tracks = AsyncMock(
        side_effect=lambda tracks: [
            Track(
                title=t.title,
                artists=t.artists,
                duration_ms=t.duration_ms,
                isrc=t.isrc,
                user_id=t.user_id,
                id=t.id,
                version=1,
            )
            for t in tracks
        ]
    )
    connector_repo.upsert_connector_tracks = AsyncMock(
        side_effect=lambda _connector, tracks: {
            t.connector_track_identifier: t for t in tracks
        }
    )
    connector_repo.find_tracks_by_connectors = AsyncMock(return_value=existing or {})
    connector_repo.touch_last_seen = AsyncMock(return_value=None)
    connector_repo.map_tracks_to_connectors = AsyncMock(
        side_effect=lambda specs, **_: [
            spec.track.with_connector_track_id(spec.connector, spec.connector_id)
            for spec in specs
        ]
    )
    uow.get_match_review_repository().create_reviews_batch = AsyncMock(
        side_effect=lambda reviews: reviews
    )
    return uow


def _specs(uow) -> list[ConnectorMappingSpec]:
    return uow.get_connector_repository().map_tracks_to_connectors.await_args.args[0]


def _service() -> TrackResolutionService:
    return TrackResolutionService(evaluator_config=create_matching_config())


class TestAlreadyMapped:
    async def test_a_mapped_payload_is_resolved_by_its_mapping_and_touched(self):
        canonical = make_track(title="Ibrik", artist="Bonobo", version=1)
        uow = _uow(existing={(CONNECTOR, "sp_1"): canonical})

        result = await _service().ingest(
            CONNECTOR, [_payload("sp_1")], uow, user_id=TEST_USER_ID
        )

        assert [t.id for t in result] == [canonical.id]
        assert result[0].connector_track_identifiers[CONNECTOR] == "sp_1"
        connector_repo = uow.get_connector_repository()
        connector_repo.touch_last_seen.assert_awaited_once()
        # Nothing planned, created or mapped.
        uow.get_track_repository().save_tracks.assert_not_awaited()
        connector_repo.map_tracks_to_connectors.assert_not_awaited()

    async def test_the_lock_is_taken_before_any_probe(self):
        uow = _uow()
        order: list[str] = []
        track_repo = uow.get_track_repository()
        track_repo.acquire_ingest_lock.side_effect = lambda _u: order.append("lock")
        connector_repo = uow.get_connector_repository()
        connector_repo.upsert_connector_tracks.side_effect = lambda _c, tracks: (
            order.append("upsert") or {t.connector_track_identifier: t for t in tracks}
        )

        _ = await _service().ingest(
            CONNECTOR, [_payload("sp_1")], uow, user_id=TEST_USER_ID
        )

        assert order[:2] == ["lock", "upsert"]
        track_repo.acquire_ingest_lock.assert_awaited_once_with(TEST_USER_ID)


class TestCreation:
    async def test_a_new_payload_is_created_and_mapped_as_primary(self):
        uow = _uow()

        result = await _service().ingest(
            CONNECTOR, [_payload("sp_1", isrc=ISRC)], uow, user_id=TEST_USER_ID
        )

        (saved,) = uow.get_track_repository().save_tracks.await_args.args[0]
        assert saved.isrc == ISRC
        assert saved.user_id == TEST_USER_ID
        assert saved.connector_track_identifiers[CONNECTOR] == "sp_1"
        (spec,) = _specs(uow)
        assert spec.primary is True
        assert spec.match_method == "direct"
        # Derived from the evidence, not the old constant — and the evidence
        # rides along with it.
        assert spec.confidence_evidence is not None
        assert spec.confidence == spec.confidence_evidence["final_score"]
        assert result[0].id == saved.id

    async def test_connector_track_ids_are_passed_so_payload_rows_stay_untouched(
        self,
    ):
        uow = _uow()

        _ = await _service().ingest(
            CONNECTOR, [_payload("sp_1")], uow, user_id=TEST_USER_ID
        )

        kwargs = (
            uow.get_connector_repository().map_tracks_to_connectors.await_args.kwargs
        )
        assert set(kwargs["connector_track_ids"]) == {(CONNECTOR, "sp_1")}

    async def test_repeated_payloads_collapse_to_one_row_and_one_result_each(self):
        uow = _uow()

        result = await _service().ingest(
            CONNECTOR,
            [_payload("sp_1"), _payload("sp_2", title="Kerala"), _payload("sp_1")],
            uow,
            user_id=TEST_USER_ID,
        )

        assert len(result) == 3
        assert result[0].id == result[2].id
        assert result[1].id != result[0].id
        assert len(_specs(uow)) == 2


class TestReuse:
    async def test_an_isrc_owner_is_reused_as_a_secondary_mapping(self):
        owner = make_track(
            title="Ibrik", artist="Bonobo", duration_ms=245_733, isrc=ISRC, version=1
        )
        uow = _uow(isrc_owners={ISRC: owner})

        result = await _service().ingest(
            CONNECTOR, [_payload("sp_1", isrc=ISRC)], uow, user_id=TEST_USER_ID
        )

        assert result[0].id == owner.id
        uow.get_track_repository().save_tracks.assert_not_awaited()
        (spec,) = _specs(uow)
        assert spec.track is owner
        assert spec.match_method == "isrc_match"
        assert spec.primary is False
        # The ISRC decided, so the name probe was not asked about this payload.
        uow.get_track_repository().find_tracks_by_title_artist.assert_not_awaited()

    async def test_a_name_owner_is_rekeyed_by_what_it_normalizes_to(self):
        """The probe answers on a parenthetical-stripped form; the planner
        must see the owner under its own key, not the probe pair's."""
        original = make_track(
            title="Ice Ice Baby", artist="Vanilla Ice", duration_ms=257_000, version=1
        )
        uow = _uow(
            name_owners={
                ("ice ice baby (wunderbros dubstep remix)", "vanilla ice"): original
            }
        )

        result = await _service().ingest(
            CONNECTOR,
            [
                _payload(
                    "sp_remix",
                    title="Ice Ice Baby (Wunderbros Dubstep Remix)",
                    artist="Vanilla Ice",
                    duration_ms=251_200,
                )
            ],
            uow,
            user_id=TEST_USER_ID,
        )

        assert result[0].id != original.id

    async def test_a_batch_twin_maps_onto_the_leader_created_in_the_same_call(self):
        uow = _uow()

        result = await _service().ingest(
            CONNECTOR,
            [_payload("sp_a", isrc=ISRC), _payload("sp_b", isrc="USA2B2056087")],
            uow,
            user_id=TEST_USER_ID,
        )

        assert result[0].id == result[1].id
        assert len(uow.get_track_repository().save_tracks.await_args.args[0]) == 1
        leader, follower = _specs(uow)
        assert leader.primary is True
        assert follower.primary is False
        assert follower.track.id == leader.track.id
        assert follower.match_method == "canonical_reuse"


class TestDeferral:
    async def test_a_suspect_collision_creates_without_the_isrc_and_queues_a_review(
        self,
    ):
        owner = make_track(
            title="Ibrik", artist="Bonobo", duration_ms=200_000, isrc=ISRC, version=1
        )
        uow = _uow(isrc_owners={ISRC: owner})

        result = await _service().ingest(
            CONNECTOR,
            [_payload("sp_1", isrc=ISRC, duration_ms=215_000)],
            uow,
            user_id=TEST_USER_ID,
        )

        assert result[0].id != owner.id
        (saved,) = uow.get_track_repository().save_tracks.await_args.args[0]
        assert saved.isrc is None
        review_repo = uow.get_match_review_repository()
        (reviews,) = review_repo.create_reviews_batch.await_args.args
        (review,) = reviews
        assert review.track_id == owner.id
        assert review.match_method == "isrc_suspect"
        assert review.user_id == TEST_USER_ID
        (spec,) = _specs(uow)
        assert spec.primary is True

    async def test_no_reviews_means_no_review_write(self):
        uow = _uow()

        _ = await _service().ingest(
            CONNECTOR, [_payload("sp_1")], uow, user_id=TEST_USER_ID
        )

        uow.get_match_review_repository().create_reviews_batch.assert_not_awaited()


class TestEmptyInput:
    async def test_nothing_in_nothing_out_and_no_lock(self):
        uow = _uow()

        assert await _service().ingest(CONNECTOR, [], uow, user_id=TEST_USER_ID) == []

        uow.get_track_repository().acquire_ingest_lock.assert_not_awaited()
        assert uow.get_connector_repository().mock_calls == []


class TestReturnShape:
    async def test_results_come_back_in_input_order_naming_this_batchs_ids(self):
        mapped = make_track(title="Kerala", artist="Bonobo", version=1)
        uow = _uow(existing={(CONNECTOR, "sp_known"): mapped})

        result = await _service().ingest(
            CONNECTOR,
            [_payload("sp_new"), _payload("sp_known", title="Kerala")],
            uow,
            user_id=TEST_USER_ID,
        )

        assert [t.connector_track_identifiers[CONNECTOR] for t in result] == [
            "sp_new",
            "sp_known",
        ]
        assert result[1].id == mapped.id
        touched = uow.get_connector_repository().touch_last_seen.await_args
        assert touched is not None
        assert len(touched.args[1]) == 1
        assert touched.kwargs == {"user_id": TEST_USER_ID}
