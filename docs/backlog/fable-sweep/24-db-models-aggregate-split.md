# 24 — db_models.py: split by aggregate (optional — honest ROI: med-low)

> Spoke of [The Fable Sweep](README.md) ([v0.8.12](../v0.8.12.md)). Self-contained work order — written so a fresh agent can execute it cold, without re-reading the whole codebase.

**Area:** infrastructure · **Suggested executor:** Opus · **Effort:** M · **ROI:** med-low · **Risk:** low-med · **Status:** Not Started

## Problem

`persistence/database/db_models.py` (1,516 lines) holds all 29 table models. Unlike the other seed giants, this file is **wide, not deep**: ~52 lines per class, zero oversized functions, easy to navigate by class name. The audit's honest assessment: this is the *least* urgent of the seed decompositions — a conventional single-models-module SQLAlchemy layout. It earns a spoke because the seed named it and because per-aggregate files would shrink future diffs, but the ROI is navigation/diff-hygiene only.

## Why it matters

Maintainer: smaller per-aggregate diffs (a playlist schema change doesn't touch a 1,516-line file); clearer ownership per aggregate. User: none.

## Proposed change

1. Convert to a package `persistence/database/models/`: `base.py` (DatabaseModel, TimestampMixin, BaseEntity), `track.py` (DBTrack, DBConnectorTrack, DBTrackMapping, DBMatchReview, DBTrackMetric, DBTrackLike, DBTrackPlay, DBConnectorPlay, DBTrackPreference[Event], DBTrackTag[Event]), `playlist.py` (DBPlaylist, DBConnectorPlaylist, DBPlaylistMapping, DBPlaylistTrack, DBPlaylistSyncBase, DBPlaylistAssignment[Member]), `workflow.py` (DBWorkflow[Version], DBWorkflowRun[Node], DBSchedule), `ops.py` (DBOperationRun, DBSyncCheckpoint), `auth.py` (DBOAuthToken, DBOAuthState, DBUserSettings).
2. SQLAlchemy string-based `relationship()` targets resolve via the shared registry — cross-module relationships work; verify each after the move.
3. The package `__init__.py` re-exports all models as THE canonical import path (`from src.infrastructure.persistence.database.models import DBTrack`) — this is package organization, not a compat shim, but it must be the ONLY path: update every import site (`git grep -l 'database.db_models'` — repositories, alembic `env.py`, tests) and delete `db_models.py`.
4. Alembic: confirm `env.py` imports the metadata via the package; run `uv run alembic upgrade head` + an autogenerate dry-run showing an EMPTY diff (proves the schema is unchanged).

## Blast radius & behavior-preservation

~30+ import sites; zero schema/behavior change. The empty-autogenerate-diff check is the proof.

## Test plan

Existing: full `uv run pytest` (integration suites exercise every model). Plus the alembic empty-diff check above. No new tests.

## Guardrails (do not skip)

- **Clean break:** `db_models.py` deleted; single canonical import path via the package.
- **Grep gate:** `git grep 'db_models'` returns nothing when done.
- **Green:** `uv run pytest` stays green; `uv run alembic revision --autogenerate` dry-run produces no operations (discard the generated file).
- **Scope discipline:** no column/index/constraint changes whatsoever.

## Notes / counter-proposal

Legitimate to **skip** this spoke: the audit rates the status quo acceptable. Approve only if the diff-hygiene value reads as worth ~30 import-site churn.
