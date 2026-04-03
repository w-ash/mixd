# Plan Review

Run a comprehensive review using 5 parallel reviewer agents, then synthesize findings into a prioritized verdict.

## Step 1: Determine Review Mode

Detect what's being reviewed based on user input and git state. **Ask the user if ambiguous.**

### Plan Doc Mode
Use when the user provides a document path (e.g., `docs/backlog/some-feature.md`, a plan mode file, or any `.md` spec). Read the full document content — this is what gets passed to reviewers.

### Code Review Mode
Use when the user says "review my changes" or similar, or when there's no document specified. Run `git diff` (unstaged) and `git diff --cached` (staged) to capture all uncommitted changes. If both are empty, check `git diff HEAD~1` for the latest commit. This diff is what gets passed to reviewers.

### Choosing
- If the user passes a file path → **Plan Doc Mode**
- If the user says "review changes", "review code", "review diff" → **Code Review Mode**
- If there's a plan mode file active and no other input → **Plan Doc Mode** with that file
- If ambiguous → ask the user

## Step 2: Launch All 5 Reviewers in Parallel

Launch ALL 5 reviewer agents **in a single message with multiple Agent tool calls** so they run concurrently as background agents.

The 5 reviewer agents (use `subagent_type` for each):

1. **reviewer-architect** — Architecture, layer boundaries, dependency flow, complexity
2. **reviewer-engineer** — Implementability, edge cases, code reuse, testing strategy
3. **reviewer-product** — User story alignment, persona fit, scope creep
4. **reviewer-security** — OAuth tokens, secrets, injection, input validation
5. **reviewer-qa** — Test coverage, error paths, regression risks, migration safety

### Prompt Template for Each Agent

Include in every agent's prompt:

```
You are reviewing in **[Plan Doc / Code Review]** mode.

## Content to Review

[paste the full plan document content OR the full git diff here]

## Additional Context

- Project root: /Users/awright/Projects/personal/mixd
- Read CLAUDE.md for project principles
- Scan relevant source files to validate your findings against the actual codebase

## Instructions

Review this from your perspective. Return findings ranked by severity (Critical > High > Medium > Low) using your structured output format. If everything looks good from your perspective, say so clearly.

IMPORTANT: You have a maximum of 6 turns. Budget your time — spend 3-4 turns reading and investigating, then use your remaining turns to write your structured findings. Always return your findings even if your investigation is incomplete.
```

## Step 3: Synthesize Findings

Once all 5 agents have returned, combine their findings into a single summary. **Do not wait indefinitely** — if an agent hasn't returned after the others are done, note it as "(no response)" and proceed.

### Output Format

```
## Plan Review Summary

**Mode:** [Plan Doc | Code Review]
**Reviewed:** [document name or "uncommitted changes on `branch-name`"]

### Critical Issues (must address before proceeding)
1. [Reviewer Role] Issue — reasoning

### Warnings (should address)
1. [Reviewer Role] Issue — reasoning

### Suggestions (consider)
1. [Reviewer Role] Suggestion — reasoning

### All Clear
[List reviewers with no findings]

### Verdict: [Ready to Implement | Needs Attention | Needs Rework]
[One sentence summary explaining the verdict]
```

**Verdict guidelines:**
- **Ready to Implement** — No critical or high issues. Suggestions are optional improvements.
- **Needs Attention** — Has high-severity issues or important warnings worth addressing first.
- **Needs Rework** — Has critical issues that would cause significant problems if built as-is.

Collapse any reviewer with no findings into a single "All Clear" line. If the work is solid, a short summary is fine — don't pad it.

## Agent Teams Upgrade (optional)

For richer review where agents can cross-reference and challenge each other's findings, enable Agent Teams:

```json
// settings.json
{ "env": { "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1" } }
```

Then ask: "Create an agent team with 5 reviewer teammates to review [target]." Teammates can message each other directly, debate findings, and coordinate through a shared task list. This costs ~7x more tokens but produces deeper analysis for complex plans.
