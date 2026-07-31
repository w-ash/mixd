"""Use case for withdrawing a remembered cannot-link rejection.

A rejected pair is a *sticky* cannot-link constraint: mixd refuses to
re-propose that candidate until the matcher version changes or one side's
match-relevant metadata does. That is what stops the same wrong match
reappearing on every import — but it also means a mistaken rejection is
permanent, and Reltio's production experience with exactly this store is
that entries accumulate and the system quietly *under*matches. So an escape
hatch has to exist from the start.

This was a standalone script (``scripts/unreject_mapping_candidate.py``)
until the CLI carried a real caller for ``ResolutionNegativeRepository.unreject``.
The v0.14.0 Manual Mapping UI will eventually give it a second, richer caller;
until then ``mixd tracks unreject`` is it.

Nothing is deleted: the rejection stays in the table as history, inert —
``unreject`` only stamps ``unrejected_at``, and the reversal gets its own
``unrejected`` event so it is as auditable as the original rejection.
"""

from uuid import UUID

from attrs import define

from src.domain.exceptions import NotFoundError
from src.domain.repositories.resolution import ResolutionDecision
from src.domain.repositories.uow import UnitOfWorkProtocol


@define(frozen=True, slots=True)
class UnrejectMappingCandidateCommand:
    """Parameters identifying one cannot-link pair to withdraw."""

    user_id: str
    connector_track_id: UUID
    candidate_track_id: UUID
    # Which surface asked. Recorded on the `unrejected` event, so "why is this
    # pair matchable again" answers with where the decision came from — a
    # person at the CLI, or the assistant acting on their behalf. The use case
    # cannot infer it: both callers reach the same `execute`.
    source: str = "cli"


@define(frozen=True, slots=True)
class UnrejectMappingCandidateResult:
    """Confirmation of the withdrawn rejection, with enough context to display."""

    connector_name: str
    connector_track_id: UUID
    candidate_track_id: UUID


@define(slots=True)
class UnrejectMappingCandidateUseCase:
    """Withdraw one cannot-link constraint so the pair is a candidate again."""

    async def execute(
        self, command: UnrejectMappingCandidateCommand, uow: UnitOfWorkProtocol
    ) -> UnrejectMappingCandidateResult:
        """Execute the withdrawal.

        Raises:
            NotFoundError: If the connector track doesn't exist, or no active
                rejection matches the given pair.
        """
        async with uow:
            connector_repo = uow.get_connector_repository()

            # Looked up first (not joined into the WHERE) purely for the
            # `connector_name` the event log wants — `unreject` itself keys
            # only on the two track ids, which is all the cannot-link row
            # actually stores.
            ct = await connector_repo.get_connector_track_by_id(
                command.connector_track_id
            )
            if ct is None:
                raise NotFoundError(
                    f"Connector track {command.connector_track_id} not found"
                )

            negative_repo = uow.get_resolution_negative_repository()
            withdrawn = await negative_repo.unreject(
                user_id=command.user_id,
                connector_track_id=command.connector_track_id,
                candidate_track_id=command.candidate_track_id,
            )
            if not withdrawn:
                raise NotFoundError(
                    f"No active rejection for {ct.connector_name}:"
                    f"{command.connector_track_id} x {command.candidate_track_id}"
                )

            recorder = uow.get_resolution_recorder()
            _ = await recorder.record(
                [
                    ResolutionDecision(
                        event_type="unrejected",
                        connector_name=ct.connector_name,
                        connector_track_id=command.connector_track_id,
                        track_id=command.candidate_track_id,
                        payload={"source": command.source},
                    )
                ],
                user_id=command.user_id,
            )
            await uow.commit()

            return UnrejectMappingCandidateResult(
                connector_name=ct.connector_name,
                connector_track_id=command.connector_track_id,
                candidate_track_id=command.candidate_track_id,
            )
