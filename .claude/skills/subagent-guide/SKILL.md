---
name: subagent-guide
description: When and how to use narada's specialized subagents — architecture-guardian, test-pyramid-architect, sqlalchemy-async-optimizer, log-diagnostician, react-architecture-specialist, vitest-strategy-architect. Use when delegating to subagents or choosing which agent to invoke.
user-invocable: false
---

# Subagent Usage Guide

Narada uses specialized Claude Code subagents for deep technical expertise. Main agent delegates to subagents for advisory consultation, then implements with full context.

## Available Subagents (6 Total, 3 Active at a Time)

**Backend Agents**:
1. **sqlalchemy-async-optimizer** - SQLAlchemy 2.0 async patterns, SQLite concurrency, N+1 query prevention
2. **architecture-guardian** - Clean Architecture + DDD enforcement (backend + frontend)
3. **test-pyramid-architect** - pytest strategy, async test debugging, 60/35/5 pyramid balance
4. **log-diagnostician** - Diagnose runtime failures by reading structured JSON log files

**Frontend Agents** (v0.3.0+):
5. **react-architecture-specialist** - React + TypeScript patterns, Tanstack Query, performance optimization
6. **vitest-strategy-architect** - Vitest component testing, React Testing Library, Playwright E2E

## Rotation Strategy (Maximize 3 Active)

**Current Phase** -> **Active Agents**:

**Backend-Heavy Development** (v0.2.x):
- sqlalchemy-async-optimizer
- architecture-guardian
- test-pyramid-architect

**Frontend-Heavy Development** (v0.3.0+):
- architecture-guardian (universal - always useful)
- react-architecture-specialist
- vitest-strategy-architect

**Full-Stack Development** (v0.3.0+):
- architecture-guardian (always active)
- 2 domain-specific agents (backend or frontend based on current task)

## When to Use Each Agent

### sqlalchemy-async-optimizer
**Use when**:
- Designing repository methods with complex joins/relationships
- Debugging "database locked" errors
- Optimizing `selectinload()` strategies
- Implementing batch operations efficiently

**Example**: "I need to fetch playlists with all their tracks. How should I structure the query to avoid N+1 problems?"

**Output**: Query design with `selectinload()`, rationale, performance implications

### architecture-guardian
**Use when**:
- Reviewing new use cases before implementation
- Validating refactors across multiple layers
- Self-review for architectural violations
- Designing adapters for new services

**Example**: "Review this use case for Clean Architecture violations: Does it import from infrastructure? Are repository protocols used correctly?"

**Output**: Approved / Approved with suggestions / Rejected with specific violations

### test-pyramid-architect
**Use when**:
- Designing test coverage for new features
- Debugging flaky async tests (SQLite locks, task cleanup)
- Ensuring proper fixture usage (`db_session` vs `get_session()`)
- Maintaining 60/35/5 test pyramid ratio

**Example**: "Design test strategy for SyncPlaylistUseCase. What's the unit/integration split?"

**Output**: Test plan with unit/integration breakdown, fixture recommendations, test case outlines

### react-architecture-specialist (v0.3.0+)
**Use when**:
- Designing component hierarchies
- Reviewing Tanstack Query patterns (cache configuration, stale-while-revalidate)
- Performance optimization (React.memo, useMemo, useCallback)
- State management strategy (context vs props vs query state)

**Example**: "Should my TrackList component fetch tracks from the API or receive them as props?"

**Output**: Component architecture design with container/presentational split, Tanstack Query configuration

### vitest-strategy-architect (v0.3.0+)
**Use when**:
- Designing component test strategy
- Debugging flaky async component tests
- Mocking Tanstack Query in tests
- Planning E2E test scenarios (Chromium desktop only)

**Example**: "How should I test the PlaylistCard component? What's the right mix of component tests vs integration tests?"

**Output**: Test strategy with component/integration split, mocking patterns, test case outlines

## Subagent Response Pattern

All subagents follow this structure:

1. **Analyze Context** - Understand the specific challenge
2. **Provide Solution** - Concrete, implementable recommendations with code examples
3. **Explain Rationale** - Why this approach, performance/architectural implications
4. **Anticipate Issues** - Potential pitfalls, edge cases, testing considerations
5. **Success Criteria** - How to verify the solution works correctly

## Tool Scope (Read-Only by Default)

| Agent | Read | Glob | Grep | Bash | Edit | Write |
|-------|------|------|------|------|------|-------|
| sqlalchemy-async-optimizer | Yes | Yes | Yes | Yes* | No | No |
| architecture-guardian | Yes | Yes | Yes | No | No | No |
| test-pyramid-architect | Yes | Yes | Yes | Yes* | No | No |
| react-architecture-specialist | Yes | Yes | Yes | Yes* | No | No |
| vitest-strategy-architect | Yes | Yes | Yes | Yes* | No | No |

**Bash restrictions**:
- sqlalchemy-async-optimizer: `sqlite3`, `alembic` (inspection only, no migrations)
- test-pyramid-architect: `pytest` execution, coverage analysis
- react-architecture-specialist: `vite build`, `vitest` execution
- vitest-strategy-architect: `vitest`, `playwright test` execution

**Why read-only**: Subagents provide expert guidance, main agent implements with full context. This preserves:
- Context awareness (main agent sees full picture)
- Architectural safety (subagents flag violations, don't "fix" incorrectly)
- Learning retention (main agent applies patterns consistently)

## Ad-Hoc Task Tool

For one-off investigations not warranting permanent agents, use built-in Agent tool for:
- Library research (tenacity internals, dependency evaluation)
- Minimal reproduction cases
- Temporary specialists (delete after use)

## Best Practices

**When to Use Subagents**:
- Complex architectural decisions (multiple valid approaches)
- Performance optimization (query strategies, React memoization)
- Testing strategy design (what to test, how to test)
- Debugging specialized issues (SQLite locks, async patterns)

**When to Use Main Agent Directly**:
- Simple implementations (read file, fix typo)
- Straightforward patterns (already documented in CLAUDE.md)
- When you already know the approach

**Subagent Workflow**:
1. Main agent identifies need for specialist expertise
2. Invokes subagent with specific question
3. Subagent returns focused recommendation
4. Main agent implements with full codebase context
5. (Optional) Subagent reviews implementation for compliance
