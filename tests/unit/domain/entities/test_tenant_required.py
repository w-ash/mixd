"""A tenanted entity cannot be built without naming its tenant.

``user_id`` has no default on the domain entities since v0.12.0.2: a caller
that forgets it fails at construction, not after the row has landed in a
tenant nobody owns.
"""

from uuid import uuid7

import pytest

from src.domain.entities.track import ArtistCredit, Track, TrackLike


class TestUserIdIsRequired:
    def test_track_without_user_id_raises(self):
        with pytest.raises(TypeError, match="user_id"):
            Track(title="Untenanted", artists=[ArtistCredit(credited_name="Nobody")])  # type: ignore[call-arg]

    def test_track_like_without_user_id_raises(self):
        with pytest.raises(TypeError, match="user_id"):
            TrackLike(track_id=uuid7(), service="spotify")  # type: ignore[call-arg]
