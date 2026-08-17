#!/usr/bin/env bash
# Suppression/dead-code ratchet — counts may only decrease.
# Read-only; exits 1 if any count exceeds its baseline.
# Baselines are updated in the same commit that lowers them.
set -euo pipefail
cd "$(dirname "$0")/.."

# 63 → 70 at v0.9.1: the 4 agent-parity classification frozensets (consumed by the
# parity test + capability-matrix generator, not within src/) plus 3 v0.9.0 chat
# names (voice attrs fields, a settings field) that vulture couldn't see used.
# 70 → 83 at v0.9.5: the remote-MCP OAuth AS — 8 OAuthAuthorizationServerProvider /
# TokenVerifier Protocol methods dispatched structurally by the mcp SDK's handlers,
# 3 OAuthMetadata fields set for CIMD, and 2 claim/field names (jti, _PinnedTarget.ip).
# 83 → 71 at v0.10.1: 12 entries vulture no longer flags — the names they guarded became
# genuinely used in src/, so the suppressions hid nothing. Found by diffing the whitelist
# against `vulture src/ alembic/` run without it; pruning them left the findings identical.
# 71 → 73 at v0.10.1: reset_run_activity / reset_schedule_signal are test-only
# isolation helpers. vulture's config excludes tests/, so it cannot see their
# callers and reported both as dead — failing CI's dead-code step on every run
# since they landed. Whitelisting is the honest fix; deleting them would break
# three test modules.
# 74 → 78 at v0.10.2: the identity ledger's read surfaces (get_supersession_chain,
# events_for_mapping), the un-reject repository method (its only caller is
# scripts/unreject_mapping_candidate.py, and scripts/ is outside vulture's paths), and
# SUSPECT_RECHECK_SECONDS — declared substrate for the re-validation worker the milestone
# deliberately deferred. The same commit removed 17 other flagged names by giving them
# real consumers, so the net movement is down.
# 78 → 77 post-v0.10.2: SUSPECT_RECHECK_SECONDS deleted. The id-death debounce was folded
# into the negative cache's own consecutive-miss counter, so the separate suspect state it
# paced no longer exists — the constant had nothing left to be substrate for.
# 77 → 76, 2026-07-30: `unreject` deleted from the whitelist. `mixd tracks unreject`
# (backed by a real UnrejectMappingCandidateUseCase) replaced
# scripts/unreject_mapping_candidate.py as the repository method's caller, so it now has
# an in-src call site vulture can see — confirmed by running `vulture src/ alembic/`
# (no whitelist) before removing the entry, per this file's own standing precedent.
# 76 → 78 at v0.10.2.13: size_bytes / settled_at on the import-queue entry. Both are
# written in src/ and serialized out of the queue endpoint, but every reader is in
# web/src/lib/import-queue.ts — outside vulture's paths — so it sees write-only
# attributes. Whitelisting is the honest fix: they are load-bearing for the queue view's
# estimate and per-file durations, and the alternative is an artificial Python reader.
# 78 → 82 at v0.10.3: the four ResolutionMetrics counters. v0.10.3 collapsed
# _combine_phase_results' per-metric `if x > 0: add(...)` blocks into one table-driven
# loop, because maintaining the resolution phase's counter names and the combined
# result's separately is exactly how the skip breakdown reached the logs but never the
# run record. The names now live only as strings in _RUN_METRIC_KEYS /
# _CARRIED_RESOLUTION_METRICS, which vulture cannot follow into a TypedDict key.
# 82 → 83 at v0.10.3.4: `should_exit`, sse-starlette's own shutdown flag. Our signal
# handler displaces the one the library introspects to notice a shutdown, so the lifespan
# sets its flag directly — a write to a third-party attribute whose only reader lives
# inside the library, where vulture cannot follow it.
BASE_WHITELIST=83
BASE_NOQA=13
BASE_TYPE_IGNORE=0
BASE_PYRIGHT_IGNORE=18

# `|| true`: grep exits 1 on zero matches, which is a ratchet success, not an error.
whitelist=$(grep -cvE '^\s*(#|$)' vulture_whitelist.py || true)
# Both spellings: ruff 0.15.22's RUF105 migrated `# noqa: CODE` to
# `# ruff:ignore[rule-name]`. Matching only the old form would silently count 0
# and stop guarding — the ratchet must survive the syntax change, not be reset
# by it. The migration was 1:1, so the baseline is unchanged.
noqa=$( (grep -rEn '# (noqa|ruff: ?ignore)' src/ --include='*.py' || true) | wc -l | tr -d ' ')
type_ignore=$( (grep -rEn '# type: ignore' src/ --include='*.py' || true) | wc -l | tr -d ' ')
pyright_ignore=$( (grep -rEn '# pyright: ignore' src/ --include='*.py' || true) | wc -l | tr -d ' ')

fail=0
check() { # name current baseline
  if [ "$2" -gt "$3" ]; then
    echo "RATCHET FAIL: $1 = $2 (baseline $3)"
    fail=1
  else
    echo "ok: $1 = $2 (baseline $3)"
  fi
}

check "vulture_whitelist entries" "$whitelist" "$BASE_WHITELIST"
check "noqa (src/)" "$noqa" "$BASE_NOQA"
check "type-ignore (src/)" "$type_ignore" "$BASE_TYPE_IGNORE"
check "pyright-ignore (src/)" "$pyright_ignore" "$BASE_PYRIGHT_IGNORE"

exit $fail
