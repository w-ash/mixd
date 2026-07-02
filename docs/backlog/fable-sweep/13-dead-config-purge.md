# 13 — Purge dead config: BatchConfig + pre-Fellegi-Sunter MatchingConfig fields

> Spoke of [The Fable Sweep](README.md) ([v0.8.12](../v0.8.12.md)). Self-contained work order — written so a fresh agent can execute it cold, without re-reading the whole codebase.

**Area:** domain+config · **Suggested executor:** Opus · **Effort:** S-M · **ROI:** high · **Risk:** low · **Status:** Not Started

## Problem

Two dead config surfaces, both grep-verified 2026-07-01:

1. **`BatchConfig` is entirely dead.** `config/settings.py:226-232` (`truncation_limit` — also a vulture hit) + `Settings.batch` (line ~567): **zero references in src/** outside settings.py; only `tests/unit/config/test_settings_validation.py` touches it.
2. **~14 `MatchingConfig` fields are plumbed but never read.** The matching domain moved to the Fellegi-Sunter model (`domain/matching/probabilistic.py` — "Replaces the additive base-minus-penalty model"), which reads only `identical/variation/phonetic_similarity_score`, `high_similarity_threshold`, `auto_accept_threshold`, `review_threshold`. Dead fields (declared `domain/matching/config.py:23-52`, assigned `config/factories.py:27-41`, mirrored in `settings.py:303-401`; zero algorithm reads — spot-verified `base_confidence_isrc`, `title_max_penalty`, `duration_per_second_penalty`): `base_confidence_isrc/_mbid/_artist_title`, `isrc_suspect_base_confidence`, `threshold_isrc/_mbid/_artist_title/_default`, `title_max_penalty`, `artist_max_penalty`, `duration_missing_penalty`, `duration_max_penalty`, `duration_tolerance_ms`, `duration_per_second_penalty`. The domain file even labels some "Legacy per-method thresholds".

## Why it matters

Maintainer: every dead field is threaded through THREE layers (env var → Settings → factory → domain config) — 14 fields × 3 layers of illusory tunability. Anyone "tuning" `TITLE_MAX_PENALTY` today changes nothing, silently. User: indirect — honest config surface.

## Proposed change

1. Delete `BatchConfig`, `Settings.batch`, and its test cases.
2. For each dead MatchingConfig field: delete from `domain/matching/config.py`, `config/factories.py`, `config/settings.py` (and any `.env.example`/docs mention — `git grep -i title_max_penalty` across the repo).
3. Re-run the full matching suite; the surviving fields' defaults must be untouched.
4. Before deleting each field, re-verify individually: `git grep <field>` shows only the three declaration/assignment sites + tests. Any field with a real read stays (document it in the PR).

## Blast radius & behavior-preservation

Env vars for dead fields stop being parsed — if a deployment sets them, they become inert junk in the environment (they already are, semantically). No runtime behavior change because nothing reads the values. Prod `.env`s should be tidied opportunistically (note in PR description; not a code concern).

## Test plan

Existing: `uv run pytest tests/ -k "matching or settings"` — matching-behavior tests prove the live model unaffected. Delete only the test cases that assert the dead fields' plumbing.

## Guardrails (do not skip)

- **Clean break:** all three layers per field in one commit.
- **Grep gate:** `git grep 'BatchConfig\|truncation_limit\|title_max_penalty\|base_confidence_isrc'` returns nothing when done.
- **Layer flow:** unchanged; domain `MatchingConfig` stays frozen attrs.
- **Green:** `uv run pytest` stays green.
- **Ratchet:** removes the `truncation_limit` vulture hit; whitelist shrinks.
- **Scope discipline:** the Fellegi-Sunter live fields and their thresholds are sacrosanct — behavior-preserving means match confidences do not move by a single point.

## Notes / counter-proposal

None.
