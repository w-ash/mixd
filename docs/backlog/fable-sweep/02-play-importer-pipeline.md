# 02 — Play-importer pipeline: typed params, single username resolution, dead paths out

> Spoke of [The Fable Sweep](README.md) ([v0.8.12](../v0.8.12.md)). Self-contained work order — written so a fresh agent can execute it cold, without re-reading the whole codebase.

**Area:** infrastructure · **Suggested executor:** Fable · **Effort:** L · **ROI:** high · **Risk:** med · **Status:** Not Started

## Problem

The play-import template stack (`src/infrastructure/services/base_play_importer.py` 573 lines, `src/infrastructure/connectors/lastfm/play_importer.py` 688 — the largest connector file, 5 suppressed-PLR violations — and `src/infrastructure/connectors/spotify/play_importer.py` 220) has four compounding structural defects:

1. **`**kwargs: object` param soup.** The whole pipeline (`import_data` → `_run_import_pipeline` → `_fetch_data`/`_process_data`/`_handle_checkpoints`) threads untyped kwargs, then re-types them at each hop via `_extract_common_params` (base:531-572) with `cast(...)` and dict-filtering. The TypedDicts (`CommonImportParams`, `LastFMImportParams`, `SpotifyImportParams`, base:29-51) exist but are only applied *after* the untyped hand-off, e.g. `lastfm/play_importer.py:92` `typed_params = cast(LastFMImportParams, {**common_params, **lastfm_params})`. This is a dense cluster of the basedpyright `reportUnknown*` warnings Story 3 wants to promote to errors.
2. **Redundant username fallbacks that undercut the security fix.** `import_plays` resolves the username ONCE, token-first (`lastfm/play_importer.py:98,144-174` — the docstring calls the token-first precedence "the security crux" closing a cross-tenant env leak). But two later layers re-run the env fallback anyway: `_load_checkpoint:288` (`username or self.lastfm_connector.lastfm_username`) and `_fetch_date_range_strategy:361` (same). Since the resolved username is passed down, these fallbacks are dead-in-practice — yet they are exactly the code path that would reintroduce the leak if a refactor ever dropped the argument.
3. **Pass-through layer.** `_import_plays_unified` (lastfm:178-209) extracts three kwargs and forwards everything unchanged to `import_data` — pure indirection.
4. **Half-dead base machinery.** Both subclasses call `super().__init__(None)`, so `plays_repository` is always `None`; both override `_save_data` to use `_save_connector_plays_via_uow`. The base `_save_data` (base:389-431, with its `bulk_insert_plays` + pre-approved pyright-ignore) is unreachable, as is the `plays_repository` attribute. The `_store_connector_plays`/`_get_stored_connector_plays` pair (base:350-364) returns results via mutable instance state instead of return values. Also: ~15 "MIGRATED from …" archaeology comments contradicting the repo's own comment conventions.

## Why it matters

Maintainer: the import pipeline is the Weekly Curator's data-reclamation core (flows 4.2/4.3); today its control flow can't be type-checked and its security invariant (token-first account resolution, multi-tenancy) relies on convention rather than structure. User: indirect — reliability of history imports; removes the latent cross-tenant fallback path.

## Proposed change

1. **Type the pipeline.** Replace `**kwargs: object` with explicit typed params: `import_data(batch_id, progress_emitter, uow, params: LastFMImportParams | SpotifyImportParams)` — or better, make `BasePlayImporter[TRawData, TParams]` generic over a frozen attrs params object per service. Delete `_extract_common_params` and the boundary casts.
2. **Single username resolution.** `_resolve_username` stays the only resolution point; `_load_checkpoint` and `_fetch_date_range_strategy` take `username: str` (required) and lose their `or self.lastfm_connector.lastfm_username` fallbacks.
3. **Delete `_import_plays_unified`**; `import_plays` calls `import_data` directly.
4. **Remove dead base machinery:** `plays_repository` attribute, base `_save_data` (make abstract or move `_save_connector_plays_via_uow` to be THE save path), `__init__(None)` ceremony. Have `import_plays` return connector plays directly instead of the `_store_connector_plays` instance-state handoff if feasible without breaking `PlayImporterProtocol`.
5. **Strip "MIGRATED" comments** and the paragraph-length debug-log blocks in `_fetch_date_range_strategy` (lastfm:420-424, 463-468, 493-507 keep the boundary-violation warning, drop the narration).

## Blast radius & behavior-preservation

Callers: `application/services/play_import_orchestrator.py` (resolves importers via `play_import_registry`), CLI `history import-lastfm`, web import routes (`interface/api/routes/imports.py`). The `PlayImporterProtocol` signature (`import_plays(uow, progress_emitter, **params)`) is the outer boundary — keep it or update its few call sites in the same pass. Checkpoint semantics, chunking behavior, dedup, and filtering must be byte-identical; the only removed *behavior* is the unreachable env-fallback inside already-resolved paths.

## Test plan

Existing: `tests/` play-import suites (`uv run pytest tests/ -k "play_import or lastfm_import or spotify_import"`). Before refactor, add a characterization test pinning `_resolve_username` precedence (token → request → env → raises `LastfmAuthRequiredError`) if not already covered — this is the security invariant. Reuse `make_mock_uow`.

## Guardrails (do not skip)

- **Clean break:** no shims/aliases/re-export layers; one import path per thing; update **every** call site.
- **Grep gate:** `git grep '_import_plays_unified\|_extract_common_params'` returns nothing when done.
- **Layer flow:** inward-only; `base_play_importer` stays in infrastructure/services; connectors keep importing it.
- **Green:** `uv run pytest` stays green; no test weakened to pass.
- **Ratchet:** this spoke should clear `lastfm/play_importer.py`'s 5 PLR violations — verify with `uv run ruff check src/infrastructure/connectors/lastfm/play_importer.py --select PLR0912,PLR0915,PLR0914,PLR1702,PLR0913,PLR0917,PLR0911`; if the whole `PLR0914` class clears repo-wide it flips in Story 3's closeout, not here.
- **Scope discipline:** the Last.fm checkpoint keyed by `user_id=<lastfm username>` (lastfm:576-581) is a naming/tenancy question — logged in the hub's Deferred section, do NOT change it here.

## Notes / counter-proposal

Depends on nothing; pairs naturally with spoke 03 (play resolvers) if one agent takes both — same subsystem, same test suite.
