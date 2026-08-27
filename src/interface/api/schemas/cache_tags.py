"""Cache-invalidation tag vocabulary — what a long operation stales (v0.11.4).

One table, keyed by ``operation_type``, because ``operation_type`` is already the
granular name of *what ran*: every ``launch_sse_operation`` caller passes one,
every ``OperationRun`` row stores one, and the scheduler mints ``sync:<target>``.
Nothing needs declaring at a callsite, and no operation can run without naming
itself.

Tags name **resource families the API serves** — not endpoints, not tables — so
this lives in the interface layer beside the wire schemas that carry it, next to
``OperationStatusLiteral``. Values track the generated Orval router groups so the
web client's path-to-tag rules stay mechanical, with three deliberate splits:
``connector-playlists`` (it proxies a live third-party API — never refetch it on
spec), ``workflow-catalog`` (the node catalog is the largest static payload in the
app and no mutation dirties it), and ``checkpoints``/``import-queue`` (the rest of
``imports`` is the upload queue, whose keys the client already owns).

The ``Literal`` is load-bearing: it is what makes the generated OpenAPI enum, and
therefore the web client's ``CacheTag`` union, one vocabulary rather than two.

**Asymmetric by design.** An extra tag costs one refetch of an already-mounted
query; a missing tag costs a user reading stale data and reloading. Every row
below errs toward including.
"""

from collections.abc import Mapping
from typing import Final, Literal

from src.application.use_cases._shared.sync_targets import SYNC_TARGETS

CacheTag = Literal[
    "assistant",
    "checkpoints",
    "connector-playlists",
    "connectors",
    "import-queue",
    "operation-runs",
    "playlist-assignments",
    "playlists",
    "plays",
    "reviews",
    "schedules",
    "settings",
    "stats",
    "tags",
    "tracks",
    "workflow-catalog",
    "workflow-runs",
    "workflows",
]

# Every SSE-tracked operation appends an ``operation_runs`` row, so Import
# History is stale the moment any of them settles. Folded into the shared
# constants rather than repeated on fifteen rows.
_RUN_LOG: Final[tuple[CacheTag, ...]] = ("operation-runs",)

# A play import ingests plays, materialises canonical tracks, and advances the
# service's sync checkpoint — and that checkpoint is where the connector card's
# ``last_synced_at`` comes from (``routes/connectors.py::_last_synced_by_service``),
# which is exactly the non-obvious edge a per-callsite key list keeps missing.
_PLAY_IMPORT: Final[tuple[CacheTag, ...]] = (
    *_RUN_LOG,
    "plays",
    "tracks",
    "checkpoints",
    "connectors",
    "stats",
)

# A likes import writes liked-state onto canonical tracks and advances the likes
# checkpoint. No plays ingested.
_LIKES_IMPORT: Final[tuple[CacheTag, ...]] = (
    *_RUN_LOG,
    "tracks",
    "checkpoints",
    "connectors",
    "stats",
)

# Anything moving tracks between a canonical playlist and a connector one: the
# playlist, its entries, its links, and the browse row's ``import_status``.
_PLAYLIST_WRITE: Final[tuple[CacheTag, ...]] = (
    *_RUN_LOG,
    "playlists",
    "connector-playlists",
)

_LAUNCHED_TOUCHES: Final[Mapping[str, tuple[CacheTag, ...]]] = {
    "import_lastfm_history": _PLAY_IMPORT,
    "import_spotify_recent": _PLAY_IMPORT,
    "import_apple_recent": _PLAY_IMPORT,
    # GDPR-export file import — same ingest path (``run_import``), same writes.
    "import_spotify_history": (*_PLAY_IMPORT, "import-queue"),
    "import_spotify_likes": _LIKES_IMPORT,
    # Pushes local likes OUT to Last.fm. Nothing local changes but the export
    # checkpoint, and therefore the card's freshness line.
    "export_lastfm_likes": (*_RUN_LOG, "checkpoints", "connectors"),
    # Creates a canonical Playlist + PlaylistLink per imported playlist; new
    # tracks land canonically too, and the library totals move.
    "import_connector_playlists": (*_PLAYLIST_WRITE, "tracks", "stats"),
    # Writes tag-driven assignments through to the connector's playlists; the
    # applied tags become visible on the tag list.
    "apply_assignments_bulk": (*_PLAYLIST_WRITE, "tags", "playlist-assignments"),
    "sync_playlist_link": _PLAYLIST_WRITE,
    # Reprojects the play ledger, which restates every track's play counts —
    # and the library filters on those (``min_plays``, ``played_within_days``),
    # so ``tracks`` is not optional here. ``dry_run`` writes nothing — tagged
    # anyway; see the module docstring's asymmetry note.
    "rebuild_play_history": (*_RUN_LOG, "plays", "tracks", "stats"),
    # Workflow runs write ``workflow_runs``, not ``operation_runs`` — hence no
    # ``operation-runs`` tag — and reach destinations, which are playlists.
    "workflow_run": ("workflow-runs", "workflows", "playlists", "tracks", "stats"),
    # A preview skips DESTINATION writes only: its source nodes still commit
    # canonical rows (see application-patterns.md § Preview use cases), so a
    # preview that pulls in new tracks moves the library and the dashboard. It
    # is a deliberate toolbar action, not a keystroke, so the refetch is cheap.
    "workflow_preview": ("tracks", "stats"),
}

# Scheduled fires (``sync_target_runner``) run the same imports but additionally
# move ``last_run_at`` and ``consecutive_failures`` on the schedule row. They have
# no SSE stream, so these reach the client only through the run-log read path.
#
# Derived, not spelled: a scheduled fire runs the work its target's
# ``operation_type`` names, so its tags are that operation's plus ``schedules``.
# ``.get`` rather than ``[]``: an untagged target must fail the guard test with
# its own message, not a KeyError while importing this module — which every SSE
# and run-log path imports, so the API would not start and the test that names
# the fix could not even be collected.
_SCHEDULED_TOUCHES: Final[Mapping[str, tuple[CacheTag, ...]]] = {
    f"sync:{target}": (
        *_LAUNCHED_TOUCHES.get(spec.operation_type, ()),
        "schedules",
    )
    for target, spec in SYNC_TARGETS.items()
}

OPERATION_TOUCHES: Final[Mapping[str, tuple[CacheTag, ...]]] = {
    **_LAUNCHED_TOUCHES,
    **_SCHEDULED_TOUCHES,
}


# Families whose own rows advance once per completed child of a parented run —
# the import queue's manifest, and nothing else. A parent stream carries these
# per item; the full set rides the parent's own terminal, once.
PER_ITEM_TOUCHES: Final[frozenset[CacheTag]] = frozenset({"import-queue"})


def per_item_touches_for(operation_type: str) -> tuple[CacheTag, ...]:
    """The subset of ``operation_type``'s tags cheap enough to fire per child.

    A thirteen-file export would otherwise invalidate plays, tracks and stats
    thirteen times over on the drain's stream.
    """
    return tuple(tag for tag in touches_for(operation_type) if tag in PER_ITEM_TOUCHES)


def touches_for(operation_type: str) -> tuple[CacheTag, ...]:
    """Which resource families ``operation_type`` stales. Unknown yields empty.

    Unknown is silent on purpose: inventing tags for an untagged operation would
    be the guess this table exists to replace. ``test_cache_tags.py`` stops the
    unknown set from growing.
    """
    return OPERATION_TOUCHES.get(operation_type, ())
