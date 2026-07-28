#!/usr/bin/env python3
"""Withdraw one remembered rejection so the pair can be matched again.

A rejected pair is a *sticky* cannot-link constraint: mixd refuses to re-propose
that candidate until the matcher version changes or one side's match-relevant
metadata does. That is what stops the same wrong match reappearing on every
import — but it also means a mistaken rejection is permanent, and Reltio's
production experience with exactly this store is that entries accumulate and the
system quietly *under*matches. So an escape hatch has to exist from the start.

The v0.14.0 Manual Mapping UI will own this. Until then it is this script.

Everything here reads except one UPDATE, which stamps ``unrejected_at`` on the
single matching row and appends an ``unrejected`` event so the reversal is as
auditable as the rejection was. Nothing is deleted: the rejection stays in the
table as history, inert.

Usage:
    # See what this user currently has suppressed
    uv run python scripts/unreject_mapping_candidate.py --list --user default

    # Withdraw one, by the ids the listing prints
    uv run python scripts/unreject_mapping_candidate.py \\
        --user default \\
        --connector-track <connector_track_uuid> \\
        --candidate-track <track_uuid>
"""

import asyncio
import sys
from uuid import UUID

from sqlalchemy import select

from src.config import setup_script_logger
from src.domain.repositories.resolution import ResolutionDecision
from src.infrastructure.persistence.database import get_session
from src.infrastructure.persistence.database.db_models import (
    DBConnectorTrack,
    DBResolutionNegative,
    DBTrack,
)
from src.infrastructure.persistence.database.user_context import user_context
from src.infrastructure.persistence.repositories.resolution import (
    ResolutionNegativeRepository,
)
from src.infrastructure.services.resolution_recorder import ResolutionRecorder

_DEFAULT_USER = "default"
_LIST_LIMIT = 50


def _argument(args: list[str], flag: str, default: str | None = None) -> str | None:
    """Value following ``flag``, or ``default`` when the flag is absent."""
    if flag not in args:
        return default
    index = args.index(flag) + 1
    return args[index] if index < len(args) else default


async def _list_rejections(user_id: str) -> None:
    """Print this user's active cannot-link entries with enough context to act."""
    # Every table read here is FORCE ROW LEVEL SECURITY, and the policy reads
    # ``app.user_id`` from the transaction — which the ``after_begin`` hook
    # copies out of this contextvar. Without the context the session runs as
    # the default tenant, so ``--user`` would silently list (and withdraw)
    # nothing for any real user.
    with user_context(user_id):
        async with get_session() as session:
            result = await session.execute(
                select(
                    DBResolutionNegative.connector_track_id,
                    DBResolutionNegative.candidate_track_id,
                    DBResolutionNegative.connector_name,
                    DBResolutionNegative.matcher_version,
                    DBConnectorTrack.connector_track_identifier,
                    DBTrack.title,
                )
                .join(
                    DBConnectorTrack,
                    DBConnectorTrack.id == DBResolutionNegative.connector_track_id,
                )
                .join(DBTrack, DBTrack.id == DBResolutionNegative.candidate_track_id)
                .where(
                    DBResolutionNegative.user_id == user_id,
                    DBResolutionNegative.kind == "rejected_pair",
                    DBResolutionNegative.unrejected_at.is_(None),
                )
                .order_by(DBResolutionNegative.created_at.desc())
                .limit(_LIST_LIMIT)
            )
            rows = result.tuples().all()

    if not rows:
        print(f"No active rejections for user {user_id!r}.")
        return

    print(f"Active rejections for {user_id!r} (newest {len(rows)}):\n")
    for ct_id, track_id, connector, version, identifier, title in rows:
        print(f"  {connector}:{identifier}  ✗  {title}")
        print(f"    --connector-track {ct_id} --candidate-track {track_id}")
        print(f"    matcher_version={version}\n")


async def _unreject(user_id: str, connector_track_id: UUID, track_id: UUID) -> None:
    """Withdraw one rejection and record the withdrawal."""
    # See ``_list_rejections``: without the tenancy context the UPDATE runs as
    # the default tenant and withdraws nothing for a real ``--user``.
    with user_context(user_id):
        async with get_session() as session:
            connector_name = await session.scalar(
                select(DBConnectorTrack.connector_name).where(
                    DBConnectorTrack.id == connector_track_id
                )
            )
            if connector_name is None:
                print(f"No connector track {connector_track_id}.")
                return

            # The one mutation, delegated to the repository so what counts as an
            # "active" rejection stays single-sourced with the resolution path.
            withdrawn = await ResolutionNegativeRepository(session).unreject(
                user_id=user_id,
                connector_track_id=connector_track_id,
                candidate_track_id=track_id,
            )
            if not withdrawn:
                print("No active rejection matched those ids — nothing to withdraw.")
                return

            _ = await ResolutionRecorder(session).record(
                [
                    ResolutionDecision(
                        event_type="unrejected",
                        connector_name=connector_name,
                        connector_track_id=connector_track_id,
                        track_id=track_id,
                        payload={"source": "unreject_mapping_candidate.py"},
                    )
                ],
                user_id=user_id,
            )
            await session.commit()
            print(
                f"Withdrew rejection {connector_name}:{connector_track_id} x {track_id}. "
                "The pair is a candidate again on the next resolution pass."
            )


async def main() -> None:
    setup_script_logger("unreject_mapping_candidate")

    args = sys.argv[1:]
    user_id = _argument(args, "--user", _DEFAULT_USER) or _DEFAULT_USER

    if "--list" in args:
        await _list_rejections(user_id)
        return

    connector_track = _argument(args, "--connector-track")
    candidate_track = _argument(args, "--candidate-track")
    if not connector_track or not candidate_track:
        print(__doc__)
        return

    await _unreject(user_id, UUID(connector_track), UUID(candidate_track))


if __name__ == "__main__":
    asyncio.run(main())
