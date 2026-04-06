---
name: review
description: Run parallel reviewer agents on plans, diffs, features, or layers. Use when the user asks to review code, a plan, or audit a feature or layer.
argument-hint: "[target — file path, feature name, layer name, or 'diff']"
user-invocable: true
disable-model-invocation: true
allowed-tools: Agent Bash Read Glob Grep
---

# Review

Run specialized reviewer agents in parallel, then synthesize findings into a prioritized verdict.

Supports four review modes: **plan** (design documents), **diff** (uncommitted changes), **vertical** (feature audit across layers), and **horizontal** (layer audit across features).

## Pre-injected Context

Current branch: !`git branch --show-current`
Uncommitted changes: !`git diff --stat 2>/dev/null || echo "(no changes)"`

## Step 1: Determine Review Mode

Parse the user's input (available as `$ARGUMENTS`) and determine the mode. Use the pre-injected context above to inform mode detection — if there are uncommitted changes and no explicit target, default to diff mode.

### Mode Detection Logic

1. **File path argument** (e.g., `docs/backlog/v0.7.md`, a plan mode file) → **plan** mode
2. **No argument**, or argument is "diff", "changes", "code" → **diff** mode
3. **Argument matches a feature name** in `.claude/review.yaml` `features:` → **vertical** mode
4. **Argument matches a layer name** in `.claude/review.yaml` `layers:` → **horizontal** mode
5. **Plan mode file is active** and no other input → **plan** mode with that file
6. **Ambiguous** → ask the user

To check feature/layer names: read `.claude/review.yaml` if it exists. If the file doesn't exist, only plan and diff modes are available unless the user explicitly describes what they want reviewed.

### Gather Content Per Mode

**Plan mode:**
- Read the plan/document file
- The full content will be embedded in each agent's prompt

**Diff mode:**
- Run `git diff` (unstaged) and `git diff --cached` (staged)
- If both empty, use `git diff HEAD~1` for the latest commit
- Also run `git diff --stat` for the summary

**Vertical mode:**
- Read `.claude/review.yaml` to get the feature's file list and description
- Expand any glob patterns in the file list using the Glob tool
- The file list will be passed to agents (they read the files themselves)

**Horizontal mode:**
- Read `.claude/review.yaml` to get the layer's pattern and description
- Expand the glob pattern to get the full file list
- For large layers, provide the file list and let agents sample

## Step 2: Read Project Configuration

Read `.claude/review.yaml` if it exists. Extract:
- `layers:` — for scope-based agent selection (diff mode) and horizontal reviews
- `features:` — for vertical reviews
- `personas:` — include in the product reviewer's prompt

If `.claude/review.yaml` does not exist, the system still works:
- Plan and diff modes work with just CLAUDE.md
- Vertical and horizontal modes require either the manifest or explicit user guidance about which files to review

## Step 3: Select Agents

| Mode | Agents to launch |
|------|-----------------|
| plan | All 5: reviewer-architect, reviewer-engineer, reviewer-security, reviewer-product, reviewer-qa |
| diff | Scope-based selection (see below) |
| vertical | All 5 (feature audit needs every perspective) |
| horizontal | reviewer-architect, reviewer-engineer, reviewer-qa |

### Scope-Based Selection (diff mode only)

Categorize changed file paths using the `layers:` patterns from `review.yaml`:

- If changes are **only in one layer** → launch agents most relevant to that layer
- If changes span **multiple layers** or are **unclear** → launch all 5
- Always include **reviewer-qa** if any code files changed
- Always include **reviewer-security** if auth, API, or connector files changed
- Include **reviewer-product** if user-facing files changed (CLI, API routes, frontend)

If no `review.yaml` exists, fall back to file extension heuristics:
- `*.py` backend files → architect, engineer, security, qa
- `*.ts`/`*.tsx` frontend files → engineer, security, qa
- `*.md` docs → product
- Mixed → all 5

## Step 4: Launch Agents in Parallel

Launch ALL selected agents **in a single message with multiple Agent tool calls** so they run concurrently as background agents.

### Prompt Templates

#### Plan Mode

IMPORTANT: Read the plan file yourself and embed the full content into each agent's prompt.

```
You are reviewing in **Plan** mode.

## Content to Review

[paste the full plan document content here]

## Instructions

All context you need is provided above. You may use Grep for quick spot-checks if needed, but focus on evaluating the plan against your rules and the project's principles in CLAUDE.md.

[If review.yaml has personas, add for product reviewer:]
## Project Personas
[paste personas section]

Return your structured report ending with ### Verdict:.
```

#### Diff Mode

```
You are reviewing in **Diff** mode.

## Changed Files

[paste git diff --stat here]

## Full Diff

[paste the full git diff here]

## Instructions

Review the changes from your perspective. Read CLAUDE.md for project principles. You may read the changed files for full context.

Return your structured report ending with ### Verdict:.
```

#### Vertical Mode

```
You are auditing the **[feature name]** feature from your perspective.

## Feature Description
[from review.yaml features.[name].description]

## Files in Scope
[list of expanded file paths]

## Instructions

Read the files in scope. Evaluate this feature end-to-end from your perspective. Check that it follows project principles from CLAUDE.md. Look for consistency, correctness, and completeness across the full feature.

[If review.yaml has the feature's persona, add:]
## Target Persona
[persona name]: [persona description]

Return your structured report ending with ### Verdict:.
```

#### Horizontal Mode

```
You are auditing the **[layer name]** layer from your perspective.

## Layer Description
[from review.yaml layers.[name].description]

## Files in Scope
[list of expanded file paths — or a representative sample if > 30 files]

## Instructions

Read a representative sample of files in this layer. Look for consistency issues, pattern violations, and opportunities for improvement. Check that all files follow the same conventions per CLAUDE.md and any applicable rules in .claude/rules/.

Return your structured report ending with ### Verdict:.
```

## Step 5: Synthesize Findings

Once all agents have returned, combine findings into a single summary. **Do not wait indefinitely** — if an agent hasn't returned after the others are done, note it as "(no response)" and proceed.

### Synthesis Process

1. Parse `### Verdict:` from each agent's report
2. Deduplicate overlapping findings across reviewers
3. Sort violations by severity and group suggestions by theme
4. Collect observations and positive notes
5. Preserve reviewer-specific sections: Product's persona/story mapping, QA's missing test scenarios

### Verdict Aggregation

- Any agent reports **REJECTED** → overall **Needs Rework**
- Any agent reports **APPROVED WITH SUGGESTIONS** (and none REJECTED) → overall **Needs Attention**
- All agents report **APPROVED** → overall **Ready to Implement** (plan/diff) or **Healthy** (vertical/horizontal)

### Output Format

```
## Review Summary

**Mode:** [Plan | Diff | Vertical: feature-name | Horizontal: layer-name]
**Reviewed:** [document name, "uncommitted changes on `branch`", feature name, or layer name]
**Reviewers:** [list which reviewers ran]

### Critical Issues (must address)
1. [Reviewer Role] Issue — reasoning

### Warnings (should address)
1. [Reviewer Role] Issue — reasoning

### Suggestions (consider)
1. [Reviewer Role] Suggestion — reasoning

### All Clear
[List reviewers with no findings]

### Verdict: [Ready to Implement | Needs Attention | Needs Rework | Healthy]
[One sentence summary explaining the verdict]
```

**Verdict guidelines:**
- **Ready to Implement / Healthy** — No violations from any reviewer. Suggestions are optional.
- **Needs Attention** — Has suggestions worth addressing first but no blocking violations.
- **Needs Rework** — Has violations that would cause significant problems.

Collapse any reviewer with no findings into a single "All Clear" line. If the work is solid, a short summary is fine — don't pad it.
