---
name: subagent-guide
description: Catalog of mixd's subagents and specialist skills, and when to route work to each. Use when choosing who to delegate to.
user-invocable: false
---

# Subagent & Specialist Guide

Mixd splits specialist work between two mechanisms based on whether isolated context is needed:

- **Subagents** (in `.claude/agents/`) — run in separate context windows, best for tasks that read many files or produce output the main context shouldn't absorb raw (log dumps, review reports).
- **Specialist skills** (in `.claude/skills/`) — consultation-style domain experts whose full guidance loads into the main context only on invocation. Use for design opinions and pattern guidance.

Main agent delegates for advisory consultation, then implements with full context.

## Subagents

### Task / isolated-execution (2)

- **log-diagnostician** — Diagnose runtime failures by reading structured JSON log files. Isolated context prevents raw log data from filling the main conversation.
- **workflow-manager** — Create, update, validate, and debug `mixd workflow` definitions. Runs CLI commands that mutate workflow state.

### Reviewers (5) — launched via `/review` only

- **reviewer-architect** — architecture compliance, layer boundaries, dependency flow, complexity budget
- **reviewer-engineer** — implementability, edge cases, existing code reuse, testing strategy
- **reviewer-product** — user story alignment, persona fit, workflow completeness, scope creep
- **reviewer-security** — auth handling, secret exposure, injection risks, dependency safety
- **reviewer-qa** — test coverage gaps, error paths, regression risks, migration safety

Read-only background agents (`permissionMode: plan`, `background: true`, Sonnet with `effort: medium`). Not invoked individually — use `/review` to launch. A `require-review-report.sh` Stop hook enforces `### Verdict:` output format. Supports four modes: plan reviews, diff reviews, vertical feature audits, horizontal layer audits. See `.claude/review.yaml` for project-specific review dimensions.

## Specialist skills

These used to be subagents but consume unnecessary tokens as always-loaded subagent descriptions. Their content now loads only when invoked.

- **architecture-guardian** — Clean Architecture + DDD compliance (backend + frontend)
- **sqlalchemy-async-optimizer** — SQLAlchemy 2.0 async patterns, concurrency, repository design
- **test-pyramid-architect** — pytest strategy, async test debugging, 60/35/5 pyramid
- **react-architecture-specialist** — React + TypeScript, component architecture, Tanstack Query (v0.3.0+)
- **vitest-strategy-architect** — Vitest, React Testing Library, Playwright E2E (v0.3.0+)

Invoke by describing the problem to match the skill's description, or explicitly with `/<skill-name>`.

## When to delegate vs. do it yourself

**Use a specialist (subagent or skill) when:**
- Multiple valid approaches need weighing (query strategy, component architecture)
- Performance optimization decisions (selectinload vs joinedload, React.memo placement)
- Testing strategy (what to test, at which layer)
- Debugging specialized issues (lock errors, async patterns)

**Do it yourself when:**
- Simple implementations (read file, fix typo)
- Straightforward patterns already documented in CLAUDE.md or layer rules
- You already know the approach

## Ad-hoc Agent tool

For one-off investigations not warranting permanent specialists, use the generic `Agent` tool with `subagent_type: Explore` (research) or `Plan` (design):
- Library research
- Minimal reproduction cases
- Temporary scoped investigations that shouldn't pollute main context

## Specialist response pattern

All specialists follow this structure:

1. **Analyze context** — understand the specific challenge
2. **Provide solution** — concrete, implementable recommendations with code examples
3. **Explain rationale** — why this approach, performance/architectural implications
4. **Anticipate issues** — pitfalls, edge cases, testing considerations
5. **Success criteria** — how to verify the solution works correctly
