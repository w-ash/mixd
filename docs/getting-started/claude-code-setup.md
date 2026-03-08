# Claude Code Setup

Everything you need to configure Claude Code for maximum effectiveness: the `CLAUDE.md` project brain, auto-formatting hooks, permission controls, layer enforcement rules, specialist subagents, and reference skills.

---

## CLAUDE.md — The Project Brain

`CLAUDE.md` is the single most important file for Claude Code. It's loaded as the system prompt for every conversation, so it directly determines code quality.

### Recommended Sections

```markdown
# Project Name

## What This Project Does
[2-3 sentences: user problem, solution approach]

## Core Principles (YOU MUST FOLLOW)
- **Python 3.14+ Required** - Modern syntax, type safety
- **Ruthlessly DRY** - No code duplication
- **Immutable Domain** - Pure transformations, no side effects
- **Batch-First** - Collections over single items
- [Your project-specific principles]

## Architecture
**Dependency Flow**: Interface → Application → Domain ← Infrastructure

[Layer descriptions with directory mappings]

## Essential Commands
[Dev, test, lint, format, type-check commands]

## Required Coding Patterns
### Python 3.14+ Syntax (REQUIRED)
[DO / DON'T examples — see Python Tooling guide]

## Testing
### Self-Check (after every implementation)
1. Did I write tests? If not, write them now
2. Right level? Domain=unit, UseCase=unit+mocks, Repository=integration
3. Beyond happy path? Error cases, edge cases, validation
4. Using existing factories from tests/fixtures/?
5. Tests pass? `poetry run pytest tests/path/to/test_file.py -x`

## Documentation Map
[Links to deeper docs]
```

### Writing Style Tips

- **Use imperative language**: "YOU MUST FOLLOW" — Claude treats CLAUDE.md as authoritative instructions
- **Keep it under 300 lines** — lines beyond that risk context truncation; link to deeper docs
- **Include a Self-Check pattern** — a checklist Claude runs after every implementation to catch its own gaps
- **Be specific about commands** — include the exact `poetry run` prefix, flag combinations, etc.

---

## .claude/ Directory Configuration

### settings.json — PostToolUse Hooks

Hooks run automatically after Claude uses the Edit or Write tools. This ensures every file Claude touches is instantly formatted and linted.

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          {
            "type": "command",
            "command": "jq -r '.tool_input.file_path // empty' | xargs -I{} poetry run ruff check {} --fix --quiet 2>/dev/null; exit 0"
          }
        ]
      },
      {
        "matcher": "Edit|Write",
        "hooks": [
          {
            "type": "command",
            "command": "jq -r '.tool_input.file_path // empty' | grep -E '\\\\.(tsx?|jsx?)$' | xargs -I{} pnpm --prefix web exec biome check --write {} 2>/dev/null; exit 0"
          }
        ]
      }
    ]
  }
}
```

**How it works**:
- `jq` extracts the file path from Claude's tool input JSON
- First hook: runs `ruff check --fix` on any Python file edit
- Second hook: runs `biome check --write` on TypeScript/JavaScript edits (filtered by `grep`)
- `exit 0` ensures hook failures never block Claude's workflow
- `2>/dev/null` suppresses noise from files outside the tool's scope

### settings.local.json — Permissions Matrix

This file is **gitignored** — it's per-developer. It controls what Claude can do without asking.

```json
{
  "permissions": {
    "allow": [
      "Bash(poetry run pytest:*)",
      "Bash(poetry run ruff check:*)",
      "Bash(poetry run ruff format:*)",
      "Bash(poetry run basedpyright:*)",
      "Bash(pnpm:*)",
      "Bash(git status:*)",
      "Bash(git diff:*)",
      "Bash(git log:*)",
      "Bash(ls:*)",
      "Bash(find:*)",
      "Bash(grep:*)",
      "WebSearch"
    ],
    "deny": []
  }
}
```

**Principle**: allow read-only operations and dev tooling by default; require confirmation for destructive operations (git push, file deletion, etc.).

---

## rules/ — Path-Based Enforcement

Rules activate automatically when Claude reads or edits files matching their `paths` glob. Each file uses YAML frontmatter to specify its target.

**`rules/domain-purity.md`**:
```markdown
---
paths:
  - "src/domain/**"
---
# Domain Layer Rules
- NEVER import from infrastructure, application, or interface layers
- All entities use immutable data classes (frozen=True)
- All transformations must be pure (no side effects, no I/O)
- Repository interfaces are Protocol classes only (zero implementation)
```

**`rules/application-patterns.md`**:
```markdown
---
paths:
  - "src/application/**"
---
# Application Layer Rules
- NEVER import from infrastructure directly — use Protocol interfaces
- Use case owns transaction boundaries (commit/rollback)
- All use cases run through the runner function
- Constructor injection for all dependencies
```

**`rules/infrastructure-patterns.md`**:
```markdown
---
paths:
  - "src/infrastructure/**"
---
# Infrastructure Layer Rules
- NEVER expose ORM models to application layer — convert to domain entities
- Validate at the boundary: raw data → typed models at API clients
- Batch operations for all repository methods (save_batch, get_by_ids)
```

**`rules/interface-patterns.md`**:
```markdown
---
paths:
  - "src/interface/**"
---
# Interface Layer Rules
- NEVER access repositories directly — call execute_use_case()
- Zero business logic in route handlers — delegate to use cases
- Route handlers are 5-10 lines maximum
```

**`rules/test-patterns.md`**:
```markdown
---
paths:
  - "tests/**"
---
# Test Rules
- Markers auto-applied by directory: tests/unit/ → unit, tests/integration/ → integration
- Use existing factory functions from tests/fixtures/
- Test names: test_<scenario>_<expected_behavior>
- Minimum coverage: happy path + at least one error/edge case per public function
```

**`rules/web-frontend-patterns.md`**:
```markdown
---
paths:
  - "web/**"
---
# Web Frontend Rules
- Three component layers: ui/ (primitives), shared/ (composites), pages/ (routes)
- Server state via Tanstack Query — no Redux/Zustand
- TypeScript strict mode — no any, no @ts-ignore
- API hooks auto-generated by Orval — never hand-edit generated/
- Co-located tests: Component.tsx → Component.test.tsx
```

**`rules/implementation-completeness.md`**:
```markdown
---
paths:
  - "src/**"
---
# Implementation Completeness
- Every source change requires corresponding tests
- After implementing, verify a test file exists at the mirror path
- Minimum coverage: happy path + at least one error/edge case
- Run tests after implementation
```

---

## agents/ — Specialist Subagents

Agents are read-only specialists that Claude invokes for deep analysis. They recommend — the main agent implements. Start with two essential agents.

**Agent metadata format** (YAML frontmatter):
```markdown
---
name: architecture-guardian
description: Use this agent when you need architectural review for Clean Architecture compliance
model: sonnet
allowed_tools: ["Read", "Glob", "Grep"]
---
```

**`agents/architecture-guardian.md`** — validates layer dependencies, repository patterns, transaction boundaries. Outputs: Approved / Approved with suggestions / Rejected with violations.

**`agents/test-pyramid-architect.md`** — designs test strategies, identifies correct test level per layer, recommends fixture patterns. Targets: 60% unit / 35% integration / 5% E2E.

**When to add more agents**: when you find yourself repeatedly giving the same specialized guidance (e.g., ORM optimization, frontend testing patterns, log analysis).

---

## skills/ — Reference Documents

Skills are embedded reference documents. Two types:

**Non-invocable** (background context, loaded automatically when relevant):
```markdown
---
name: api-contracts
description: REST API endpoint reference — routes, schemas, error codes
user-invocable: false
---
# API Contracts
[Condensed reference content]
```

**Invocable** (step-by-step workflows triggered by users):
```markdown
---
name: new-module
description: Step-by-step guide for adding a new module to the project
---
# Adding a New Module
## Step 1: Create domain entities...
## Step 2: Define repository protocol...
```

Use skills for: API contract references, design system tokens, database schema docs, repeatable multi-step workflows.
