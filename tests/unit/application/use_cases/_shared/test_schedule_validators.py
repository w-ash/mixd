"""Unit tests for the shared schedule validators.

Pure function that needs an external lib: IANA timezone membership. (Cadence
range checks live with the entity — see tests/unit/domain/test_schedule_entity.py.)
No I/O, no mocks.
"""

import pytest

from src.application.use_cases._shared.schedule_validators import (
    validate_iana_timezone,
)


class TestValidateIanaTimezone:
    def test_valid_zone(self) -> None:
        assert validate_iana_timezone("America/Los_Angeles") == "America/Los_Angeles"

    def test_abbreviation_rejected(self) -> None:
        with pytest.raises(ValueError, match="unknown IANA timezone"):
            validate_iana_timezone("PST")

    def test_bogus_zone_rejected(self) -> None:
        with pytest.raises(ValueError, match="unknown IANA timezone"):
            validate_iana_timezone("Mars/Phobos")
