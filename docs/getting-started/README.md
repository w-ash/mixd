# Getting Started: Python 3.14 + FastAPI + React with Claude Code

**A bootstrap guide for new full-stack projects — March 2026 best practices**

This guide covers everything you need to go from zero to a production-ready Python / FastAPI / React + Tailwind monorepo, optimized for development with Claude Code. It distills battle-tested patterns from real projects into copy-pasteable configurations and templates.

**Target audience**: Solo developers or small teams building full-stack web applications in 2026.

**Core philosophy**:
- **Ruthlessly DRY** — no code duplication in single-maintainer codebases
- **Immutable domain** — pure transformations, no side effects
- **Batch-first** — design for collections, single items are degenerate cases
- **Test-mandatory** — a feature is not done until tests pass
- **Validate at boundaries** — typed models at entry points, trust internals

---

## Topic Guides

Pick and choose what's relevant to your project:

| Guide | What It Covers | When You Need It |
|---|---|---|
| [Project Structure](project-structure.md) | Directory layout, Clean Architecture layers, bootstrap checklist | Every project — start here |
| [Claude Code Setup](claude-code-setup.md) | CLAUDE.md, .claude/ hooks, rules, agents, skills | Every project using Claude Code |
| [Python Tooling](python-tooling.md) | Poetry, Ruff, BasedPyright, pre-commit, Python 3.14+ patterns | Every Python project |
| [FastAPI Backend](fastapi-backend.md) | Clean Architecture, use case runner, routes, error envelope, OpenAPI | Web API backends |
| [React Frontend](react-frontend.md) | Vite, TypeScript, Biome, Orval, Tanstack Query, test utilities | React frontends consuming your API |
| [CLI with Typer](cli-typer.md) | Typer + Rich app structure, async bridge, menus, progress display | CLI tools and interactive terminals |
| [Testing Strategy](testing-strategy.md) | Test pyramid, placement by layer, factories, coverage targets | Every project — read after your stack guide |
| [Backlog Planning](backlog-planning.md) | Version-based roadmap, epic/story format, planning templates | Structured project planning |
| [CI/CD](ci-cd.md) | GitHub Actions, Claude Code @mention workflow | GitHub-hosted projects |

### Quick-Start Path

If starting from scratch, read in this order:

1. **[Project Structure](project-structure.md)** — understand the layout
2. **[Claude Code Setup](claude-code-setup.md)** — configure your AI assistant
3. **[Python Tooling](python-tooling.md)** — set up your dev environment
4. **Pick your stack**: [FastAPI](fastapi-backend.md), [React](react-frontend.md), [CLI](cli-typer.md)
5. **[Testing Strategy](testing-strategy.md)** — write tests from day one
6. **[Backlog Planning](backlog-planning.md)** — organize your roadmap
7. **[CI/CD](ci-cd.md)** — automate quality gates

---

## Essential Commands Cheat Sheet

```bash
# ── Backend ────────────────────────────────────
poetry run pytest                           # Fast tests only
poetry run pytest -m ""                     # All tests (including slow)
poetry run pytest tests/unit/ -x            # Unit tests, stop on first failure
poetry run ruff check . --fix               # Lint + autofix
poetry run ruff format .                    # Format
poetry run basedpyright src/                # Type check

# ── Frontend ───────────────────────────────────
pnpm --prefix web dev                       # Vite dev server (port 5173)
pnpm --prefix web test                      # Vitest component tests
pnpm --prefix web check                     # Biome lint + tsc type check
pnpm --prefix web build                     # Production build
pnpm --prefix web generate                  # Orval codegen from openapi.json

# ── CLI ────────────────────────────────────────
poetry run my-app --help                    # Show CLI help
poetry run my-app command --verbose         # Run with debug logging

# ── Quality Gates (run before committing) ──────
poetry run ruff check . --fix && poetry run ruff format . && poetry run basedpyright src/ && poetry run pytest
pnpm --prefix web check && pnpm --prefix web test
```
