#!/bin/bash
# Enforce structured report output from reviewer subagents.
# Stop hooks in subagent frontmatter auto-convert to SubagentStop.
# Uses exit-code 2 to block the stop when the report is missing.
# Unlike the prior version, this does NOT check stop_hook_active — it blocks
# on every attempt until ### Verdict: appears. The agent's maxTurns is the
# natural infinite-loop breaker.

INPUT=$(cat)
LAST_MSG=$(echo "$INPUT" | jq -r '.last_assistant_message // ""')

# Report present — allow stop
if echo "$LAST_MSG" | grep -q '### Verdict:'; then
  exit 0
fi

# No report — block via exit 2 (stderr becomes Claude's feedback)
cat >&2 <<'MSG'
STOP. You have NOT written your review report.
Do NOT call any tools. Your next message must be ONLY text — your structured report ending with ### Verdict:.
If your investigation is incomplete, report what you found so far. Partial findings beat no report.
MSG
exit 2
