"""Live-by-default reads for the append-only ``track_mappings`` table (v0.10.2).

Migration 044 made mappings append-only: a retired mapping stays in the table
with ``superseded_at`` set, pointing at its successor. Every reader that is not
explicitly asking for history must therefore see live rows only — one
half-aware reader means a corrected mapping's ghost resurfaces in the user's
library forever (Wikidata's rule: deprecated rows are never used unless
specifically requested).

Two mechanisms, because one cannot cover both statement styles:

- **ORM statements** are filtered here, once, by a ``do_orm_execute`` listener
  that injects ``with_loader_criteria``. It covers entity selects, column
  selects, joins, subqueries and — via ``propagate_to_loaders`` — relationship
  loads, which is why relationship/column *loads* are skipped below (the
  criteria already travelled with the originating statement).
- **Core statements** (``update()``, ``delete()``, textual SQL) are NOT
  covered by loader criteria at all. Those must carry :func:`live_only`
  explicitly; ``tests/unit/infrastructure/persistence/test_live_rows_conformance.py``
  fails the build if a mapping statement in the persistence layer omits it.

Core statements also bypass the **identity map**: a session that already loaded
a mapping (or a track's ``mappings`` collection) keeps serving the retired copy
after a supersession, because nothing told it otherwise. :func:`expire_mapping_identity`
is the targeted antidote — expire exactly the affected objects rather than
``populate_existing`` on every hot track read.

Opt out per statement with ``.execution_options(include_superseded=True)``.

The hard write-side guard is neither of these: it is the partial unique index
``uq_track_mappings_live_connector``.
"""

from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import ColumnElement, event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import ORMExecuteState, Session, with_loader_criteria
from sqlalchemy.orm.util import identity_key

from src.infrastructure.persistence.database.db_models import DBTrack, DBTrackMapping

# Execution option that turns the filter off for one statement.
INCLUDE_SUPERSEDED = "include_superseded"


def live_only(model_cls: type[DBTrackMapping]) -> ColumnElement[bool]:
    """Return the live-rows predicate for a Core statement on ``model_cls``.

    Core ``update``/``delete``/textual statements bypass the ORM listener
    entirely, so every one that assumes "one row per connector track" has to
    say so itself::

        update(DBTrackMapping).where(..., live_only(DBTrackMapping))
    """
    return model_cls.superseded_at.is_(None)


def _live_mapping_criteria(cls: type[DBTrackMapping]) -> ColumnElement[bool]:
    """Loader criteria for ``DBTrackMapping``.

    A module-level function rather than an inline lambda: ``with_loader_criteria``
    caches compiled criteria per callable, and a lambda rebuilt on every
    statement defeats that (and cannot be pickled).
    """
    return cls.superseded_at.is_(None)


def _apply_live_rows_filter(state: ORMExecuteState) -> None:
    """``do_orm_execute`` hook: scope ORM reads of ``DBTrackMapping`` to live rows."""
    if not (state.is_select and state.is_orm_statement):
        return
    if state.is_column_load or state.is_relationship_load:
        # Deferred-column and relationship loads inherit the criteria from the
        # statement that spawned them (propagate_to_loaders); re-adding it here
        # would only duplicate the predicate.
        return
    if state.execution_options.get(INCLUDE_SUPERSEDED, False):
        return
    state.statement = state.statement.options(
        with_loader_criteria(
            DBTrackMapping, _live_mapping_criteria, include_aliases=True
        )
    )


def register_live_rows_filter() -> None:
    """Register the live-rows listener on the sync ``Session`` class, once.

    **One target, always.** The listener goes on ``sqlalchemy.orm.Session``
    itself — the base class every ``AsyncSession`` delegates to, and the class
    SQLAlchemy propagates session events from to every subclass. Taking the
    target as a parameter is what made the idempotence guard a fiction: a
    caller passing ``factory.class_.sync_session_class`` and a caller passing
    the default would register against two different objects, ``event.contains``
    would see neither, and every ORM statement in the process would carry the
    criteria twice (the same hazard documented for the RLS listener in
    ``tests/integration/connectors/lastfm/test_lastfm_checkpoint_rls.py``).
    """
    if not event.contains(Session, "do_orm_execute", _apply_live_rows_filter):
        event.listen(Session, "do_orm_execute", _apply_live_rows_filter)


def expire_mapping_identity(
    session: AsyncSession,
    *,
    mapping_ids: Iterable[UUID] = (),
    track_ids: Iterable[UUID] = (),
) -> None:
    """Drop stale in-session copies after a Core-level mapping mutation.

    ``update()``/``delete()``/textual SQL write rows the ORM never sees, so a
    ``DBTrackMapping`` already in the identity map keeps its pre-supersession
    attributes, and a ``DBTrack`` whose ``mappings`` collection was loaded keeps
    serving the *retired* row for the rest of the transaction — the live-rows
    listener cannot help, because no query is emitted at all.

    Targeted on purpose: expiring exactly the touched objects, rather than
    forcing ``populate_existing`` on every hot track read, keeps the cost
    proportional to what actually changed.
    """
    sync_session = session.sync_session
    identities = sync_session.identity_map
    for mapping_id in mapping_ids:
        instance = identities.get(identity_key(DBTrackMapping, mapping_id))
        if instance is not None:
            sync_session.expire(instance)
    for track_id in track_ids:
        instance = identities.get(identity_key(DBTrack, track_id))
        if instance is not None:
            sync_session.expire(instance, ["mappings"])


__all__ = [
    "INCLUDE_SUPERSEDED",
    "expire_mapping_identity",
    "live_only",
    "register_live_rows_filter",
]
