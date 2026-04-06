# Review System

A composable, multi-agent code review system for Claude Code. Runs specialized reviewer agents in parallel across four modes: plan reviews, diff reviews, vertical feature audits, and horizontal layer audits.

## How It Works

Three mechanisms ensure agents always produce structured reports:

1. **Prompt framing** — agents have two separate budgets: investigation turns (read/search freely) and a reserved report turn that investigation cannot consume
2. **Stop hook** — a shell script blocks agent termination unless `### Verdict:` is present in the output, forcing one continuation attempt
3. **Frontmatter limits** — `effort: low`, `model: sonnet`, `maxTurns: 16`, read-only tools

Five reviewer roles run in parallel, each with a different lens:

| Agent | Focus |
|-------|-------|
| **Architect** | Layer boundaries, dependency flow, domain purity, complexity |
| **Engineer** | Implementability, edge cases, code reuse, missing steps |
| **Security** | Auth handling, input validation, secret exposure, injection |
| **Product** | Persona fit, user story alignment, scope creep, UX |
| **QA** | Test coverage, error paths, regression risk, migration safety |

## Review Modes

```
/review docs/backlog/v0.7.md     # Plan — review a design document
/review                           # Diff — review uncommitted changes
/review likes-sync                # Vertical — audit a feature across all layers
/review domain                    # Horizontal — audit a layer across all features
```

The orchestrator auto-detects mode from the argument. Plan and diff modes work in any project. Vertical and horizontal modes require a review manifest (`.claude/review.yaml`).

## Setup for Any Project

### Minimum (plan + diff reviews)

Copy these files into your project:

```
.claude/
  agents/
    reviewer-architect.md
    reviewer-engineer.md
    reviewer-security.md
    reviewer-product.md
    reviewer-qa.md
  hooks/
    require-review-report.sh    # chmod +x
  skills/
    review/
      SKILL.md
```

No project-specific configuration needed. Agents read `CLAUDE.md` at runtime for project context.

### Optional: Review Manifest (vertical + horizontal audits)

Create `.claude/review.yaml` to define how your project can be sliced:

```yaml
layers:
  backend:
    pattern: "src/**/*.py"
    description: "Python backend"
  frontend:
    pattern: "app/**/*.tsx"
    description: "React frontend"

features:
  auth:
    description: "Authentication and authorization"
    files:
      - "src/auth/**"
      - "app/components/auth/**"
    persona: "end user"

personas:
  end-user: "Non-technical person using the product daily"
```

**`layers`** — named groups with glob patterns. Used for horizontal audits and diff-mode agent selection.

**`features`** — named groups with explicit file lists. Used for vertical audits. Files can include globs.

**`personas`** — optional. Passed to the product reviewer for persona-fit evaluation.

### The Stop Hook

`require-review-report.sh` blocks agent termination unless the report is present. Requires `jq`.

```bash
#!/bin/bash
INPUT=$(cat)

# Don't loop — if we already forced one continuation, let it stop
if [ "$(echo "$INPUT" | jq -r '.stop_hook_active // false')" = "true" ]; then
  exit 0
fi

# Report present — allow stop
if echo "$INPUT" | jq -r '.last_assistant_message // ""' | grep -q '### Verdict:'; then
  exit 0
fi

# No report — block stop, force one more turn
echo "STOP reading files. You have used all your investigation turns. You MUST now write your final report using the structured format ending with ### Verdict:. If you found no issues, report APPROVED. Do NOT read any more files." >&2
exit 2
```

Register in each agent's frontmatter:

```yaml
hooks:
  Stop:
    - hooks:
        - type: command
          command: "bash .claude/hooks/require-review-report.sh"
```

## Mixd Configuration

See `.claude/review.yaml` for mixd's 6 layers, 7 features, and 3 personas. Layer-specific coding rules in `.claude/rules/` auto-load when agents read files in those layers.

## Token Efficiency

Agents use `model: sonnet`, `effort: low`, `permissionMode: plan` (read-only), and `maxTurns: 16`. The extra headroom ensures the Stop hook can always force a report turn even if investigation uses all natural turns. Agents parallelize file reads within turns for efficiency. The skill embeds content in agent prompts to avoid 5 agents reading the same file.

## Report Format

Every agent produces:

```
## [Role] Review

### Verdict: APPROVED | APPROVED WITH SUGGESTIONS | REJECTED

### Violations (must fix)
1. **[FILE:LINE]** — [rule] — [description] — [fix]

### Suggestions (should fix)
1. **[FILE:LINE]** — [description] — [why]

### Observations
- [patterns, praise, or systemic concerns]
```

The orchestrator aggregates verdicts: any REJECTED = Needs Rework, any APPROVED WITH SUGGESTIONS = Needs Attention, all APPROVED = Ready to Implement.
