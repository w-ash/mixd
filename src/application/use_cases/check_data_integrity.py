"""Check data integrity across the music library.

Runs a suite of consistency checks (primary mapping violations, orphaned
connector tracks, duplicate tracks, stale reviews) and produces a structured
report. Read-only — never modifies data.
"""

from attrs import define

from src.config.constants import IntegrityConstants
from src.domain.entities import CheckStatus
from src.domain.entities.integrity import IntegrityCheckResult, IntegrityReport
from src.domain.repositories import UnitOfWorkProtocol


@define(frozen=True, slots=True)
class CheckDataIntegrityCommand:
    """Parameterless — exists for API uniformity."""


@define(slots=True)
class CheckDataIntegrityUseCase:
    """Run all integrity checks and produce a report."""

    async def execute(
        self, command: CheckDataIntegrityCommand, uow: UnitOfWorkProtocol
    ) -> IntegrityReport:
        async with uow:
            connector_repo = uow.get_connector_repository()
            track_repo = uow.get_track_repository()
            review_repo = uow.get_match_review_repository()

            checks: list[IntegrityCheckResult] = []

            # 1. Multiple primary mappings (should never happen — fail)
            multi_primaries = await connector_repo.find_multiple_primary_violations()
            checks.append(
                IntegrityCheckResult(
                    name="multiple_primary_mappings",
                    status=_status_for(len(multi_primaries), fail_threshold=1),
                    count=len(multi_primaries),
                    details=multi_primaries,
                )
            )

            # 2. Missing primary mappings (auto-heal usually catches these — warn)
            missing_primaries = await connector_repo.find_missing_primary_violations()
            checks.append(
                IntegrityCheckResult(
                    name="missing_primary_mappings",
                    status=_status_for(len(missing_primaries), warn_threshold=1),
                    count=len(missing_primaries),
                    details=missing_primaries,
                )
            )

            # 3. Orphaned connector tracks
            orphan_count = await connector_repo.count_orphaned_connector_tracks()
            checks.append(
                IntegrityCheckResult(
                    name="orphaned_connector_tracks",
                    status=_status_for(orphan_count, warn_threshold=1),
                    count=orphan_count,
                )
            )

            # 4. Duplicate tracks (same title/artist/album fingerprint)
            duplicates = await track_repo.find_duplicate_tracks_by_fingerprint()
            checks.append(
                IntegrityCheckResult(
                    name="duplicate_tracks",
                    status=_status_for(len(duplicates), warn_threshold=1),
                    count=len(duplicates),
                    details=duplicates,
                )
            )

            # 5. Stale pending reviews (older than threshold)
            stale_count = await review_repo.count_stale_pending(
                IntegrityConstants.STALE_REVIEW_DAYS
            )
            checks.append(
                IntegrityCheckResult(
                    name="stale_pending_reviews",
                    status=_status_for(stale_count, warn_threshold=1),
                    count=stale_count,
                )
            )

            # 6. Total pending reviews (informational — never fails)
            pending_count = await review_repo.count_pending()
            checks.append(
                IntegrityCheckResult(
                    name="pending_reviews",
                    status="pass",
                    count=pending_count,
                )
            )

            overall = _worst_status(checks)
            return IntegrityReport(checks=checks, overall_status=overall)


def _status_for(
    count: int,
    *,
    warn_threshold: int = 0,
    fail_threshold: int = 0,
) -> CheckStatus:
    """Determine check status based on anomaly count and thresholds."""
    if fail_threshold and count >= fail_threshold:
        return "fail"
    if warn_threshold and count >= warn_threshold:
        return "warn"
    return "pass"


def _worst_status(checks: list[IntegrityCheckResult]) -> CheckStatus:
    """Return the worst status across all checks."""
    statuses = {c.status for c in checks}
    if "fail" in statuses:
        return "fail"
    if "warn" in statuses:
        return "warn"
    return "pass"
