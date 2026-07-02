# 23 — base_repo.py: split mapper machinery from the repository base

> Spoke of [The Fable Sweep](README.md) ([v0.8.12](../v0.8.12.md)). Self-contained work order — written so a fresh agent can execute it cold, without re-reading the whole codebase.

**Area:** infrastructure · **Suggested executor:** Opus · **Effort:** S · **ROI:** med · **Risk:** low · **Status:** Not Started

## Problem

`persistence/repositories/base_repo.py` (1,091 lines) contains two distinct concerns:

1. **Mapper machinery** (lines 49-258, ~210 lines): `ModelMapper` protocol, `SessionAwareMapper`, `BaseModelMapper`, `SimpleMapperFactory` — the DB-model ↔ domain-entity conversion layer.
2. **`BaseRepository`** (lines 259-1091, ~830 lines): query builders, relationship loading, CRUD, the two-phase upsert + savepoint bulk-upsert machinery (three `bulk_upsert` overloads, `_upsert_two_phase`, `_bulk_upsert_in_savepoint`).

Every concrete repository imports both, but they are separable: mappers never reference `BaseRepository`.

## Why it matters

Maintainer: 1,091 lines is past the navigate-comfortably threshold for the single most-inherited module in the persistence layer; the mapper/repository seam is the natural cut. User: none.

## Proposed change

1. New `persistence/repositories/mappers.py`: move `ModelMapper`, `SessionAwareMapper`, `BaseModelMapper`, `SimpleMapperFactory` (and their helpers) verbatim.
2. `base_repo.py` keeps `BaseRepository` (+ its private upsert machinery) and imports mappers from the new module.
3. Update every concrete repository/mapper import (`git grep -l 'from src.infrastructure.persistence.repositories.base_repo import'` — rewrite each to import mapper names from `mappers` and repository names from `base_repo`). No re-export from `base_repo`.

## Blast radius & behavior-preservation

Pure move: zero logic changes. ~15-20 import sites across `persistence/repositories/**` and tests. The `# pyright: ignore[reportAny]` carve-outs move with their lines (they are pre-approved for this layer; count must not grow — `scripts/check_ratchet.sh` bounds it).

## Test plan

Existing: full `uv run pytest` (any missed import fails collection immediately). No new tests — move-only.

## Guardrails (do not skip)

- **Clean break:** no `from .mappers import *` re-export in base_repo; each file imports what it uses.
- **Grep gate:** `git grep 'base_repo import.*Mapper'` returns nothing when done.
- **Layer flow:** unchanged.
- **Green:** `uv run pytest` stays green.
- **Ratchet:** `scripts/check_ratchet.sh` `BASE_PYRIGHT_IGNORE` count unchanged.
- **Scope discipline:** do NOT refactor the upsert machinery itself — dense but framework-quality; move-only this pass.

## Notes / counter-proposal

None.
