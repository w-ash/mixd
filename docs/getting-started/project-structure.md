# Project Structure

The recommended monorepo layout with Clean Architecture layers. Adjust to your project's needs — not every project needs all layers.

---

## Directory Layout

```
my-project/
├── .claude/                          # Claude Code AI configuration
│   ├── settings.json                 # PostToolUse hooks (auto-format on edit)
│   ├── settings.local.json           # Permissions matrix (gitignored)
│   ├── agents/                       # Read-only specialist subagents
│   │   ├── architecture-guardian.md
│   │   └── test-pyramid-architect.md
│   ├── rules/                        # Path-based enforcement rules
│   │   ├── domain-purity.md
│   │   ├── application-patterns.md
│   │   ├── infrastructure-patterns.md
│   │   ├── interface-patterns.md
│   │   ├── test-patterns.md
│   │   ├── web-frontend-patterns.md
│   │   └── implementation-completeness.md
│   └── skills/                       # Reference docs & workflow guides
│       └── api-contracts/SKILL.md
├── .github/workflows/
│   └── claude.yml                    # Claude Code @mention trigger
├── src/                              # Python backend (Clean Architecture)
│   ├── domain/                       # Pure business logic, zero deps
│   │   ├── entities/                 # Immutable data models
│   │   ├── exceptions.py
│   │   └── repositories/            # Protocol interfaces only
│   ├── application/                  # Use case orchestration
│   │   ├── runner.py                 # execute_use_case() entry point
│   │   └── use_cases/
│   ├── infrastructure/               # External adapters & persistence
│   │   └── persistence/
│   │       └── repositories/         # Protocol implementations
│   ├── interface/                    # Presentation layer
│   │   └── api/
│   │       ├── app.py                # FastAPI application factory
│   │       ├── middleware.py         # Exception → error envelope
│   │       ├── routes/
│   │       └── schemas/              # Pydantic request/response
│   └── config/
│       ├── __init__.py
│       └── constants.py              # All project constants centralized
├── tests/                            # Mirror of src/ structure
│   ├── conftest.py                   # Root fixtures + auto-markers
│   ├── fixtures/                     # Factory helpers
│   │   ├── factories.py              # make_entity() builders
│   │   └── mocks.py                  # make_mock_uow()
│   ├── unit/                         # Fast, mocked (<100ms each)
│   │   ├── domain/
│   │   └── application/
│   └── integration/                  # Real deps (DB, HTTP)
│       ├── api/                      # Route handler tests
│       └── repositories/             # Real DB tests
├── web/                              # React frontend
│   ├── src/
│   │   ├── api/
│   │   │   ├── client.ts             # customFetch + ApiError
│   │   │   ├── query-client.ts       # QueryClient factory
│   │   │   └── generated/            # Orval codegen (never hand-edit)
│   │   ├── components/
│   │   │   ├── ui/                   # Primitives (shadcn/ui)
│   │   │   └── shared/               # Reusable composites
│   │   ├── hooks/
│   │   ├── pages/
│   │   ├── test/
│   │   │   ├── setup.ts              # MSW server bootstrap
│   │   │   └── test-utils.tsx        # renderWithProviders()
│   │   ├── App.tsx
│   │   └── main.tsx
│   ├── biome.json
│   ├── openapi.json                  # Copied from FastAPI backend
│   ├── orval.config.ts
│   ├── package.json
│   ├── tsconfig.json
│   ├── vite.config.ts
│   └── vitest.config.ts
├── docs/                             # Project documentation
│   ├── backlog/                      # Planning & roadmap
│   │   ├── README.md                 # Master version matrix
│   │   ├── v0.1.x.md                # Version-scoped epics
│   │   └── unscheduled.md            # Ideas without version assignment
│   └── completed/                    # Archive of shipped work
│       └── README.md
├── CLAUDE.md                         # Project instructions for Claude Code
├── pyproject.toml                    # Python deps + ruff + pyright + pytest
├── .pre-commit-config.yaml
└── .gitignore
```

## Key Principles

- **Monorepo** with clear backend/frontend separation (`src/` and `web/`)
- **Clean Architecture** layers: `Interface → Application → Domain ← Infrastructure`
- **Tests mirror the source tree** under `tests/unit/` and `tests/integration/`
- **All Claude Code configuration** lives in `.claude/`
- **Documentation** follows the backlog pattern (see [Backlog Planning](backlog-planning.md))

---

## Bootstrap Checklist

From zero to a running project:

1. **`poetry init`** — configure `pyproject.toml` with Python >=3.14 → [Python Tooling](python-tooling.md)
2. **Add dependencies** — fastapi, uvicorn, httpx, loguru, pydantic-settings + dev deps → [Python Tooling](python-tooling.md)
3. **Configure tooling** — `[tool.ruff]`, `[tool.basedpyright]`, `[tool.pytest.ini_options]` → [Python Tooling](python-tooling.md)
4. **Create `src/`** — with `domain/`, `application/`, `infrastructure/`, `interface/`, `config/` layers
5. **Create `tests/`** — with `unit/`, `integration/`, `fixtures/`, `conftest.py` → [Testing Strategy](testing-strategy.md)
6. **Write `CLAUDE.md`** — project overview, principles, architecture, commands → [Claude Code Setup](claude-code-setup.md)
7. **Create `.claude/settings.json`** — PostToolUse hooks for auto-format → [Claude Code Setup](claude-code-setup.md)
8. **Create `.claude/settings.local.json`** — permissions matrix, add to `.gitignore` → [Claude Code Setup](claude-code-setup.md)
9. **Create `.claude/rules/`** — path-based enforcement files → [Claude Code Setup](claude-code-setup.md)
10. **Create `.claude/agents/`** — architecture-guardian + test-pyramid-architect → [Claude Code Setup](claude-code-setup.md)
11. **Scaffold frontend** — `pnpm create vite web -- --template react-ts` → [React Frontend](react-frontend.md)
12. **Configure frontend tooling** — vite, tsconfig, biome, vitest, orval → [React Frontend](react-frontend.md)
13. **Set up API client** — `web/src/api/client.ts` + `query-client.ts` → [React Frontend](react-frontend.md)
14. **Set up test infra** — `web/src/test/setup.ts` + `test-utils.tsx` → [React Frontend](react-frontend.md)
15. **Create `.pre-commit-config.yaml`** — then run `pre-commit install` → [Python Tooling](python-tooling.md)
16. **Create `.github/workflows/claude.yml`** → [CI/CD](ci-cd.md)
17. **Set up backlog** — `docs/backlog/` with README + version files → [Backlog Planning](backlog-planning.md)
18. **Write a health check endpoint** — verify the full stack works end to end
19. **Run all quality gates** — confirm a clean baseline before your first commit
