"""Key derivation for the per-(user, service) token-refresh advisory lock.

The single-flight guard keys ``pg_advisory_xact_lock(class_id, obj_id)``
(two-int overload) off the (service, user_id) pair. Both halves must be
deterministic, distinct across pairs, and fit PostgreSQL's signed int4.
"""

from itertools import starmap
import zlib

from src.config.constants import TokenConstants
from src.infrastructure.persistence.repositories.token_refresh_lock import (
    refresh_lock_keys,
)

INT4_MIN = -(2**31)
INT4_MAX = 2**31 - 1


class TestRefreshLockKeys:
    def test_deterministic(self) -> None:
        assert refresh_lock_keys("tidal", "user-1") == refresh_lock_keys(
            "tidal", "user-1"
        )

    def test_distinct_across_service_user_pairs(self) -> None:
        pairs = [
            ("tidal", "user-1"),
            ("tidal", "user-2"),
            ("spotify", "user-1"),
            ("spotify", "user-2"),
        ]
        keys = set(starmap(refresh_lock_keys, pairs))
        assert len(keys) == len(pairs)

    def test_both_ints_fit_signed_int4(self) -> None:
        for i in range(50):
            class_id, obj_id = refresh_lock_keys(f"svc{i}", f"user-{i}")
            assert INT4_MIN <= class_id <= INT4_MAX
            assert INT4_MIN <= obj_id <= INT4_MAX

    def test_high_crc32_coerces_to_negative_int4(self) -> None:
        """A crc32 above the signed-int4 max maps to its two's-complement value."""
        service, user = next(
            (f"svc{i}", f"user-{i}")
            for i in range(1000)
            if zlib.crc32(f"svc{i}:user-{i}".encode()) >= 2**31
        )
        raw = zlib.crc32(f"{service}:{user}".encode())
        _, obj_id = refresh_lock_keys(service, user)
        assert obj_id == raw - 2**32
        assert obj_id < 0

    def test_class_id_matches_config_constant(self) -> None:
        class_id, _ = refresh_lock_keys("tidal", "user-1")
        assert class_id == TokenConstants.REFRESH_LOCK_CLASS
        # ASCII "tokn" — and it must fit a signed int4 for the two-int overload.
        assert TokenConstants.REFRESH_LOCK_CLASS == 0x746F6B6E
        assert TokenConstants.REFRESH_LOCK_CLASS <= INT4_MAX
