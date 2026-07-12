#!/usr/bin/env bash
# Suppression/dead-code ratchet — counts may only decrease.
# Read-only; exits 1 if any count exceeds its baseline.
# Baselines are updated in the same commit that lowers them.
set -euo pipefail
cd "$(dirname "$0")/.."

# 63 → 70 at v0.9.1: the 4 agent-parity classification frozensets (consumed by the
# parity test + capability-matrix generator, not within src/) plus 3 v0.9.0 chat
# names (voice attrs fields, a settings field) that vulture couldn't see used.
BASE_WHITELIST=70
BASE_NOQA=13
BASE_TYPE_IGNORE=0
BASE_PYRIGHT_IGNORE=18

# `|| true`: grep exits 1 on zero matches, which is a ratchet success, not an error.
whitelist=$(grep -cvE '^\s*(#|$)' vulture_whitelist.py || true)
noqa=$( (grep -rEn '# noqa' src/ --include='*.py' || true) | wc -l | tr -d ' ')
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
