# Project Narada — Planning

**Current Version**: 0.4.1
**Current Initiative**: Workflow Persistence & Web UI

→ [Completed milestones](../completed/) | [Unscheduled ideas](unscheduled.md)

---

## Planned Versions

Each milestone delivers a **vertical slice** — backend API + frontend page together — so every increment is testable end-to-end. Web UI development starts immediately on SQLite; PostgreSQL and deployment arrive when features are validated.

| Version | Goal | Status | Details |
|---------|------|--------|---------|
| **v0.2.7** | Advanced workflow features + DRY consolidation | ✅ Completed | [details](../completed/v0.2.x.md#v027-advanced-workflow-features) |
| **v0.3.0** | Web UI foundation + playlists + settings | ✅ Completed | [details](../completed/v0.3.x.md#v030-web-ui-foundation--playlists-vertical-slice-1) |
| **v0.3.1** | Imports + real-time progress | ✅ Completed | [details](../completed/v0.3.x.md#v031-imports--real-time-progress-vertical-slice-2) |
| **v0.3.2** | Track library + search | ✅ Completed | [details](../completed/v0.3.x.md#v032-library--search-vertical-slice-3) |
| **v0.3.3** | Dashboard + stats | ✅ Completed | [details](../completed/v0.3.x.md#v033-dashboard--stats-vertical-slice-4) |
| **v0.4.0** | Workflows (persistence, visualization, CRUD) | ✅ Completed | [details](v0.4.x.md#v040-workflow-persistence--visualization-vertical-slice-5a) |
| **v0.4.1** | Workflow execution + run history | ✅ Completed | [details](v0.4.x.md#v041-workflow-execution--run-history-vertical-slice-5b) |
| **v0.4.2** | Run-first workflow UX | 🔜 Not Started | [details](v0.4.x.md#v042-run-first-workflow-ux-vertical-slice-5c) |
| **v0.4.3** | Visual workflow editor + versioning | 🔜 Not Started | [details](v0.4.x.md#v043-visual-workflow-editor--preview-vertical-slice-5d) |
| **v0.4.4** | Connector playlist linking | 🔜 Not Started | [details](v0.4.x.md#v044-connector-playlist-linking-vertical-slice-6) |
| **v0.4.5** | CI/CD + quality hardening | 🔜 Not Started | [details](v0.4.x.md#v045-cicd--quality-hardening) |
| **v0.5.0** | PostgreSQL + deployment + OAuth | 🔜 Not Started | [details](v0.5.x.md#v050-postgresql-deployment--oauth) |
| **v0.6.0** | Apple Music + data quality | 🔜 Not Started | [details](v0.6.x.md#v060-apple-music--data-quality) |
| **v0.7.0** | Advanced workflow features | 🔜 Not Started | [details](v0.7.x.md#v070-advanced-workflow-features) |
| **v0.8.0** | LLM-assisted workflow creation | 🔜 Not Started | [details](v0.8.x.md#v080-llm-assisted-workflow-creation) |
| **v0.9.0** | First-class artists | 🔜 Not Started | [details](v0.9.x.md#v090-first-class-artists) |
| **v1.0.0** | Production-ready multi-user platform | 🔜 Not Started | [details](v1.0.x.md#v100-production-ready-multi-user-platform) |

---

## Infrastructure Readiness Matrix

Visual guide to infrastructure capabilities across version milestones (hobbyist scale: <10 users):

| Capability | v0.2.7 (CLI) | v0.3.0 (Web Local) | v0.5.0 (Deployed) | v1.0.0 (Multi-User) |
|------------|--------------|-------------------|-------------------|---------------------|
| **Testing** | ✅ pytest suite, <1min | ✅ + Vitest components | ✅ + E2E (Playwright) | ✅ Same |
| **CI/CD** | ⚠️ Manual | ⚠️ Manual | ✅ GitHub Actions | ✅ Same |
| **Deployment** | ✅ Poetry install | ✅ Local (SQLite) | ✅ Docker + Fly.io | ✅ Same |
| **Observability** | ✅ Loguru JSON logs | ✅ Same | ✅ Same | ✅ + Email alerts |
| **Authentication** | ❌ Not needed | ❌ Env var tokens | ✅ Spotify OAuth | ✅ + Email/password |
| **Database** | ✅ SQLite | ✅ SQLite | ✅ PostgreSQL | ✅ PostgreSQL |
| **Caching** | ❌ Not needed | ✅ Tanstack Query | ✅ + lru_cache | ✅ Same |
| **Security** | ✅ Env vars, secrets | ✅ + CORS (localhost) | ✅ + HTTPS | ✅ + bcrypt |

**Legend**: ✅ Ready | ⚠️ Needs work | ❌ Not needed

**Note**: Right-sized for hobbyist project (<10 users). No Redis, CDN, MFA, load testing, or enterprise observability. Focus on quality code over production infrastructure.

---

## Technology Decision Records

Key architecture & tech choices (see CLAUDE.md for migration details):

- **Python 3.14+ & attrs**: Modern type syntax (`str | None`, `class Foo[T]`), immutable domain entities with slots
- **PostgreSQL (v0.5.0)**: Migrated from SQLite for remote hosting and parallel Prefect execution. `asyncpg` driver, managed hosting via Neon/Supabase (dev) or Fly.io Postgres (prod). Repository pattern means zero application-layer code changes. Web UI developed on SQLite first (v0.3.x), migrated at deployment time.
- **Vite 6+ / Vitest**: 10x faster HMR than Webpack, native ESM + TypeScript
- **Tailwind CSS v4**: Rust engine (10x performance), @theme design tokens
- **Pydantic v2**: 5-50x faster validation, `from_attributes=True`
- **Clean Architecture + DDD**: Composable workflows, isolated APIs, testable logic (see docs/ARCHITECTURE.md)

---

## Reference

### Effort Estimates

Never estimate time, always estimate based on relative effort.

| Size    | Complexity Factors           | Criteria                                                                       |
| ------- | ---------------------------- | ------------------------------------------------------------------------------ |
| **XS**  | Well known, isolated         | Minimal unknowns, fits existing components, no dependencies                    |
| **S**   | A little integration         | Simple feature, 1-2 areas touched, low risk, clear requirements                |
| **M**   | Cross-module feature         | 3-4 areas involved, small unknowns, minor dependencies                         |
| **L**   | Architectural impact         | >=3 subsystems, integrations, external APIs, moderate unknowns                  |
| **XL**  | High unknowns & coordination | Cross-team, backend + frontend + infra, regulatory/security concerns           |
| **XXL** | High risk & exploration      | New platform, performance/security domains, prototype-first, many dependencies |

### Status Options
- Not Started
- In Progress
- Blocked
- Completed
