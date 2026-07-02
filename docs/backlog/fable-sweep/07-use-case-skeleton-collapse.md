# 07 — Collapse the five copy-pasted use-case skeletons

> Spoke of [The Fable Sweep](README.md) ([v0.8.12](../v0.8.12.md)). Self-contained work order — written so a fresh agent can execute it cold, without re-reading the whole codebase.

**Area:** application · **Suggested executor:** Fable · **Effort:** L · **ROI:** high · **Risk:** med · **Status:** Not Started

## Problem

The 60 `application/use_cases/` modules contain five hand-copied `execute()` skeletons. `_shared/` provides field validators and resolvers but **no execute-level scaffolding**, so the `async with uow: … await uow.commit(); return Result(...)` envelope is duplicated everywhere. The clusters (verified 2026-07-01):

- **A — bulk-mutate passthrough:** `delete_tag.py` (37), `rename_tag.py` (44), `merge_tags.py` (41) are the same shape bar the repo method (`rename_tag`/`merge_tags` are distinct repo ops — `track/tags.py:180,272` — but the use-case envelopes are line-for-line parallel). Also `delete_playlist_assignment.py` (43).
- **B — ownership-gate + single mutate:** `update_playlist_link.py:33-46` ≡ `delete_playlist_link.py:38-53` (`require_playlist_link` → repo call → None-check → commit).
- **C — load playlist → transform entries → save:** `add_playlist_tracks.py`, `remove_playlist_entries.py`, `reorder_playlist_entries.py` (255 lines total) — identical envelope, differing only in the pure entries transform; they even share docstring boilerplate.
- **D — mapping tamper-guard prelude:** `relink_connector_track.py:53-63` ≡ `unlink_connector_track.py:55-63` ≡ `set_primary_mapping.py:40-47` (`get_mapping_by_id` → None-check → `track_id` tamper guard).
- **E — mutate + event-log:** `tag_track.py`, `untag_track.py`, `batch_tag_tracks.py`, `set_track_preference.py` (existence check → mutate → early-return if unchanged → `add_events` → commit).

Plus one confirmed dead function: `_shared/command_validators.py:39-65` `api_batch_size_validator` — zero consumers (vulture + grep agree).

Note the standing rule (".claude/rules/application-patterns.md") *sanctions* pattern repetition across use cases — this spoke's position: keep one class per operation (the Command/Result surface stays), but stop duplicating the *envelope*; that's shareable plumbing, not intentional repetition. If the executing agent disagrees for a cluster, record why in the hub and skip that cluster.

## Why it matters

Maintainer: every new CRUD operation copies ~40 lines of envelope; drift between copies is where ownership-check bugs (multi-tenancy!) appear — cluster D's tamper guard exists in three hand-copies today. User: indirect — consistent authorization behavior across operations.

## Proposed change

Introduce small `_shared/` helpers, not a framework:

1. `_shared/mapping_guard.py`: `require_owned_mapping(connector_repo, mapping_id, track_id, user_id) -> Mapping` — replaces cluster D's prelude in 3 modules.
2. `_shared/entry_edit.py`: `async def persist_entry_change(command, uow, transform: Callable[[Playlist], list[PlaylistEntry]]) -> Playlist` — the C envelope; the three modules keep their pure transforms + Command/Result classes.
3. `_shared/event_log.py` (name it with the team's vocabulary): `apply_with_event_log(...)` for cluster E's changed/unchanged + `add_events` branching.
4. Merge cluster A into one module `tag_vocabulary.py` housing `DeleteTag/RenameTag/MergeTags` use cases (mirrors the sanctioned bundling in `workflow_crud.py` / `schedules.py`), sharing one private `_bulk_tag_mutation` helper. Same for B: a `mutate_owned_link` helper inside `_shared/playlist_resolver.py`.
5. Delete `api_batch_size_validator`.

Routes: update imports where module paths change (`interface/api/routes/tags.py`, `tracks.py`, `playlists.py`); handler bodies unchanged.

## Blast radius & behavior-preservation

Command/Result classes keep their names and fields → API schemas and Orval codegen unchanged. Only module organization + private envelopes move. Every route/CLI import updated in the same pass. Behavior byte-identical including error types/messages (NotFoundError text is asserted in tests).

## Test plan

Existing: the per-use-case unit suites (`uv run pytest tests/unit/application/use_cases/ -k "tag or link or entries or mapping or preference"`). These are the characterization net — they must pass **unmodified except for import paths**. No new tests unless a helper gains logic not covered (e.g., add one test per `_shared` helper).

## Guardrails (do not skip)

- **Clean break:** old module files for merged clusters are deleted; no re-export stubs.
- **Grep gate:** `git grep 'api_batch_size_validator'` returns nothing; `git grep "from src.application.use_cases.rename_tag"` returns nothing (path updated everywhere).
- **Layer flow:** helpers live in `use_cases/_shared/`; no new dependencies.
- **Green:** `uv run pytest` stays green; no test weakened (import-path updates only).
- **Ratchet:** no rule flip expected; check PLR0913 deltas on touched modules.
- **Scope discipline:** do NOT touch the orchestration set (`sync_likes`, `import_*`, `enrich_tracks`, `match_and_identify_tracks`, …) — they are correctly distinct. `update_canonical_playlist` is spoke 10.

## Notes / counter-proposal

`.claude/rules/application-patterns.md` may need a one-line amendment ("envelope helpers in `_shared/` are the sanctioned way to share the transaction skeleton") — propose it in the PR, don't silently contradict the rule.
