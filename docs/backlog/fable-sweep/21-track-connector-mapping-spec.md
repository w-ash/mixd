# 21 — track/connector.py: mapping-spec object + decompose the two batch monsters

> Spoke of [The Fable Sweep](README.md) ([v0.8.12](../v0.8.12.md)). Self-contained work order — written so a fresh agent can execute it cold, without re-reading the whole codebase.

**Area:** infrastructure · **Suggested executor:** Opus · **Effort:** M · **ROI:** high · **Risk:** med · **Status:** Not Started

## Problem

`persistence/repositories/track/connector.py` (1,333 lines — the largest repository, 6 suppressed-PLR violations) has two compounding issues:

1. **The 7-element positional tuple.** `map_tracks_to_connectors` (lines 401-544) takes `list[tuple[Track, str, str, str, int, dict | None, dict | None]]` — (track, connector, connector_id, match_method, confidence, metadata, confidence_evidence) — and unpacks it **twice** with positional `_` placeholders (lines 435-443 and 490-498). The two unpack sites must silently agree on positions; adding a field means touching every constructor across the codebase (`git grep 'map_tracks_to_connectors('` to enumerate builders). Classic error-prone shape and a PLR0913-family driver.
2. **Two monster methods.** `map_tracks_to_connectors` (~145 lines: build connector-track dicts → bulk upsert → id-map → build mapping dicts → manual-override filter → bulk upsert) and `ingest_external_tracks_bulk` (lines 602-777, ~175 lines). Both have clear internal phases (the comments mark them) but no extracted seams.

## Why it matters

Maintainer: this repository is the write path for ALL track identity resolution (every import/match lands here — flows 2.3, 4.x, 7.2). The positional tuple is where a silent field-order bug would corrupt mappings. User: indirect — identity-data integrity.

## Proposed change

1. Introduce `@define(frozen=True, slots=True) class ConnectorMappingSpec` (track, connector, connector_id, match_method, confidence, metadata, confidence_evidence) — in the domain repositories protocol module if the protocol mentions the tuple type, else local to the repo layer. Update the protocol signature (`domain/repositories/connector.py`) and every caller/builder of the tuples.
2. Decompose `map_tracks_to_connectors` along its phases: `_build_connector_track_rows(specs)`, `_upsert_connector_tracks(rows)`, `_build_mapping_rows(specs, id_map)`, `_filter_manual_overrides(rows)` — the public method becomes the pipeline.
3. Same treatment for `ingest_external_tracks_bulk` along its comment-marked phases.
4. `map_track_to_connector` (single-item wrapper, lines 546-599) stays; it just builds one spec.

## Blast radius & behavior-preservation

Protocol + implementation + all tuple builders (inward resolvers — `spotify/inward_resolver.py`, `lastfm/inward_resolver.py`, cross_discovery, use cases). Three-layer type propagation rule applies: update (1) domain protocol, (2) infra impl, (3) callers. SQL emitted must be identical — same upsert lookup_keys, same manual-override filter semantics (`MappingOrigin.MANUAL_OVERRIDE` rows never overwritten).

## Test plan

Existing: `uv run pytest tests/integration/ -k "connector_repo or track_mapping or map_track"` — the integration suites are the net (repository = integration level per testing rules). Add: one test pinning the manual-override filter if not covered (it is the data-protection invariant).

## Guardrails (do not skip)

- **Clean break:** tuple signature gone from protocol and impl; every builder constructs the spec.
- **Grep gate:** `git grep 'tuple\[$' src/domain/repositories/connector.py` — no 7-tuple remains; `git grep 'map_tracks_to_connectors'` callers all pass specs.
- **Layer flow:** spec class must be domain-pure if it lives in domain (attrs frozen, no infra imports).
- **Green:** `uv run pytest` stays green (integration tests need Docker running).
- **Ratchet:** this file's 6 PLR violations should drop; verify per-file.
- **Scope discipline:** `get_connector_metadata` overloads and the denormalized-id sync helpers are healthy — untouched. Spoke 15 separately makes 2 protocol methods impl-private; coordinate if both run.

## Notes / counter-proposal

None.
