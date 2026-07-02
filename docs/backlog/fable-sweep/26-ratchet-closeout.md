# 26 — Ratchet closeout: flip the suppressed rules as the sweep clears them

> Spoke of [The Fable Sweep](README.md) ([v0.8.12](../v0.8.12.md)). Self-contained work order — this is Story 3's execution spec, informed by the 2026-07-01 census. Runs LAST, after the approved spokes land.

**Area:** config/tooling · **Suggested executor:** Opus · **Effort:** M · **ROI:** high · **Risk:** low · **Status:** Executed (2026-07-02, branch `sweep-closeout-v0.8.17`; step 1 shipped v0.8.13; PLR0913/0917 decision still pending user call — see Closeout outcome below)

## Closeout outcome (2026-07-02, branch `sweep-closeout-v0.8.17`)

**Re-census at v0.8.16 — no rule reached 0; none flipped.** All seven stay off with dated rationale rewritten in `pyproject.toml`:

| Rule | 07-01 census | Closeout | Verdict |
|------|------|------|---------|
| PLR1702 | 2 | 2 | Spoke 11 DID clear executor.py; the 2 remaining are `apply_playlist_assignments.py:156` + `apple_music/error_classifier.py:152` (user-protected tree) — off, documented |
| PLR0911 | 7 | 5 | All 5 are guard/classification chains (webhook verify ×2, error classifiers ×3) where each return is a distinct outcome (log event / HTTP status / category); merging = worse code — off, documented |
| PLR0914 | 18 | 12 | sync_likes, executor, algorithms (29 locals) — off, documented |
| PLR0915 | 18 | 9 | sync_likes (108 statements), executor, play_dedup — off, documented |
| PLR0912 | 21 | 13 | sync_likes, repos, diff_engine — off, documented |
| PLR0917 | 20 | 20 | tracks 0913 — pending the user call |
| PLR0913 | 57 | 61 | Typer/FastAPI entry-point idiom dominates — pending the user call (recommendation: option 2, keep off) |

**noqa whodunit — solved.** The 14th `# noqa` (baseline 13, set 2026-06-11 in `1191128e`) landed in `d2d3a179` (v0.8.7, 2026-06-21): `ARG001` on the unused-but-contract-required `emitter` param of `_sync` (spoke 08 later moved it into `api/services/playlist_sync.py`, count-neutral). Fixed by renaming to `_emitter` (dummy-variable-rgx exemption) and deleting the noqa — count back to 13, baseline unchanged.

**Vulture cleaned + whitelist ratcheted.** 12 findings dispositioned: 8 dead `operation_summary` properties deleted (7 use-case Results never had a consumer; diff_engine's lost its last consumer when spoke 12 deleted `execution_strategies.py`); `retryable` ×2 whitelisted (frontend reads the API field); `NO_ISRC` + `added_at_dates` whitelisted as dated parked decisions (hub Deferred). 5 stale whitelist entries pruned (`operations_requested`, `is_auth_error`, `preserve_timestamps`, `max_api_calls`, `build_dict`). `check_ratchet.sh` baselines ratcheted: whitelist 79→63, pyright-ignore 20→18, noqa stays 13, type-ignore stays 0.

**Spec discrepancy:** the acceptance matrix names an "import-linter contract", but import-linter is not installed anywhere in the repo (v0.8.12's "already in place" claim was false). Deferred as a work order in the dependency-audit findings doc.

## Census (2026-07-01 baselines)

**basedpyright — the promotion is FREE.** `uv run basedpyright src/` reports **0 errors, 0 warnings** today. Promote all eight from `"warning"` to `"error"` in `pyproject.toml` immediately (`reportUnknownParameterType/VariableType/MemberType/ArgumentType/LambdaType`, `reportMissingTypeArgument`, `reportImplicitOverride`, `reportDeprecated` — lines ~290-309). This can even ship as the FIRST executed change of the sweep, locking the floor before refactors begin.

**ruff PLR — 143 violations across 7 suppressed rules** (full per-file list: run `uv run ruff check src/ --select PLR0912,PLR0915,PLR0914,PLR1702,PLR0913,PLR0917,PLR0911 --output-format concise`):

| Rule | Count | Flip condition |
|------|-------|----------------|
| PLR1702 (nested blocks) | 2 | Both in `executor.py` → flips when spoke 11 lands |
| PLR0911 (returns) | 7 | Near-clear; fix stragglers in the spokes that touch them, flip at closeout |
| PLR0914 (locals) | 18 | Mostly spokes 02/03/11/14/21/22 territory; re-measure after |
| PLR0915 (statements) | 18 | Same spokes; re-measure after |
| PLR0912 (branches) | 21 | Same + `ui.py` (spoke 25); re-measure after |
| PLR0917 (positional args) | 20 | Tracks PLR0913 |
| PLR0913 (args) | 57 | **Honesty note below** |

**PLR0913/0917 honesty:** many of the 57 are Typer command functions (every CLI option is a parameter — the framework's idiom) and FastAPI `Query(...)` handlers. Spokes 02/21/25 convert the *helper*-function offenders to param objects, but command/handler signatures will remain. Decision for the user at closeout, pick one:
1. Flip the rules with per-file-ignores for `src/interface/cli/**` + the route modules, with a rationale comment (precedent: `per-file-ignores` already carries `RUF029` for api). This is a *scoped, documented* suppression, not a dodge — but it is a suppression.
2. Keep PLR0913/0917 globally off with the existing rationale; flip only the five body-complexity rules (0912/0915/0914/1702/0911).
Recommendation: **(2)** — body complexity is the debt that regrows silently; argument count on framework entry points is idiom, and a scoped ignore adds config surface for little safety.

## The work

1. Promote the 8 basedpyright rules to `"error"` (free today — verify with a fresh run first).
2. After each approved spoke lands, re-run the PLR census; when a rule's count hits 0, flip it from the `ignore` list in `pyproject.toml` **in that spoke's commit** (per the hub guardrail). Whatever is still nonzero at closeout gets fixed here or explicitly re-documented in pyproject's ignore rationale (updated comment, dated).
3. Re-verify `uv run vulture` is clean against the whitelist after spokes 07/12/13/15 remove flagged items — prune stale whitelist entries.
4. Full matrix green: `uv run pytest -m ""` · `uv run basedpyright src/` · `uv run ruff check .` · `pnpm --prefix web check && pnpm --prefix web build` · import-linter contract.
5. Ship as the `0.8.17` closeout bump (each wave v0.8.13–v0.8.16 bumps at its own deploy): `pyproject.toml` version → `uv sync` → `pnpm --prefix web sync-api`; flip the README matrix rows to 🚀 Shipped.
6. Fable code-reviews the aggregate sweep diff (call-site completeness, behavior preservation, layer boundaries) before the bump.

## Guardrails (do not skip)

- **No new `# noqa`/suppressions to make a flip pass** — a rule flips only when the code genuinely clears it (the PLR0913 decision above is the one sanctioned exception, and it's the user's call, not the agent's).
- **Green:** the full matrix, not just the fast suite.
- **Scope discipline:** no code refactoring in this spoke beyond PLR0911 stragglers — it's a tooling/closeout pass.

## Notes / counter-proposal

The basedpyright promotion being free was the census's biggest surprise — the codebase's no-`Any` discipline already cleared it. Consider flipping it in the very first executed commit so every subsequent spoke is checked at the higher bar.
