# Project Narada — Planning

**Current Version**: 0.5.3
**Current Initiative**: v0.5.4 OAuth & Credentials — not started (Spotify web OAuth flow for headless container auth)

→ [Completed milestones](completed/) | [Unscheduled ideas](unscheduled.md)

---

## Planned Versions

Each milestone delivers a **vertical slice** — backend API + frontend page together — so every increment is testable end-to-end. Web UI development starts immediately on SQLite; PostgreSQL and deployment arrive when features are validated.

| Version | Goal | Status | Details |
|---------|------|--------|---------|
| **v0.2.7** | Advanced workflow features + DRY consolidation | ✅ Completed | [details](completed/v0.2.x.md#v027-advanced-workflow-features) |
| **v0.3.0** | Web UI foundation + playlists + settings | ✅ Completed | [details](completed/v0.3.x.md#v030-web-ui-foundation--playlists-vertical-slice-1) |
| **v0.3.1** | Imports + real-time progress | ✅ Completed | [details](completed/v0.3.x.md#v031-imports--real-time-progress-vertical-slice-2) |
| **v0.3.2** | Track library + search | ✅ Completed | [details](completed/v0.3.x.md#v032-library--search-vertical-slice-3) |
| **v0.3.3** | Dashboard + stats | ✅ Completed | [details](completed/v0.3.x.md#v033-dashboard--stats-vertical-slice-4) |
| **v0.4.0** | Workflows (persistence, visualization, CRUD) | ✅ Completed | [details](v0.4.x.md#v040-workflow-persistence--visualization-vertical-slice-5a) |
| **v0.4.1** | Workflow execution + run history | ✅ Completed | [details](v0.4.x.md#v041-workflow-execution--run-history-vertical-slice-5b) |
| **v0.4.2** | Run-first workflow UX | ✅ Completed | [details](v0.4.x.md#v042-run-first-workflow-ux-vertical-slice-5c) |
| **v0.4.3** | Visual workflow editor + versioning | ✅ Completed | [details](v0.4.x.md#v043-visual-workflow-editor--preview-vertical-slice-5d) |
| **v0.4.4** | Connector playlist linking | ✅ Completed | [details](v0.4.x.md#v044-connector-playlist-linking-vertical-slice-6) |
| **v0.4.5** | Code & test suite hardening | ✅ Completed | [details](v0.4.x.md#v045-code--test-suite-hardening) |
| **v0.4.6** | Track provenance & duplicate merge | ✅ Completed | [details](v0.4.x.md#v046-track-provenance--merge-vertical-slice-7a) |
| **v0.4.7** | Track relink & unlink | ✅ Completed | [details](v0.4.x.md#v047-track-relink--unlink-vertical-slice-7b) |
| **v0.4.8** | Usability & self-explanatory interface pass | ✅ Completed | [details](v0.4.x.md#v048-usability--self-explanatory-interface-pass) |
| **v0.4.9** | Data integrity & quality audit | ✅ Completed | [details](v0.4.x.md#v049-data-integrity--quality-audit) |
| **v0.4.10** | Cross-service play history deduplication | ✅ Completed | [details](v0.4.x.md#v0410-cross-service-play-history-deduplication) |
| **v0.4.11** | CLI unification & polish | ✅ Completed | [details](v0.4.x.md#v0411-cli-unification--polish) |
| **v0.5.0** | CI/CD + environment hardening | ✅ Completed | [details](v0.5.x.md#v050-cicd--environment-hardening) |
| **v0.5.1** | PostgreSQL migration + optimization | ✅ Completed | [details](v0.5.x.md#v051-postgresql-migration) |
| **v0.5.2** | PostgreSQL-native feature adoption | ✅ Completed | [details](v0.5.x.md#v052-postgresql-native-feature-adoption) |
| **v0.5.3** | Containerization & deployment | ✅ Completed | [details](v0.5.x.md#v053-containerization--deployment) |
| **v0.5.4** | OAuth & credentials | 🔜 Not Started | [details](v0.5.x.md#v054-oauth--credentials) |
| **v0.5.5** | Parallel execution & performance | 🔜 Not Started | [details](v0.5.x.md#v055-parallel-execution--performance) |
| **v0.6.0** | Preference system & likes migration | 🔜 Not Started | [details](v0.6.x.md#v060-preference-system--likes-migration) |
| **v0.6.1** | Tagging system | 🔜 Not Started | [details](v0.6.x.md#v061-tagging-system) |
| **v0.6.2** | Spotify playlist mapping (preference & tag import) | 🔜 Not Started | [details](v0.6.x.md#v062-spotify-playlist-mapping-preference--tag-import) |
| **v0.6.3** | Workflow integration & quick filters | 🔜 Not Started | [details](v0.6.x.md#v063-workflow-integration--quick-filters) |
| **v0.7.0** | Data quality tools | 🔜 Not Started | [details](v0.7.x.md#v070-data-quality) |
| **v0.7.1** | Apple Music connector | 🔜 Not Started | [details](v0.7.x.md#v071-apple-music-connector) |
| **v0.7.2** | Rekordbox connector + audio quality enrichment | 🔜 Not Started | [details](v0.7.x.md#v072-rekordbox-connector) |
| **v0.8.0** | Workflow & sync scheduling | 🔜 Not Started | [details](v0.8.x.md#v080-workflow--sync-scheduling) |
| **v0.8.1** | Editor polish, templates & playlist browse | 🔜 Not Started | [details](v0.8.x.md#v081-editor-polish-templates--playlist-browse) |
| **v0.9.0** | LLM-assisted workflow creation | 🔜 Not Started | [details](v0.9.x.md#v090-llm-assisted-workflow-creation) |
| **v0.10.0** | First-class artists | 🔜 Not Started | [details](v0.10.x.md#v0100-first-class-artists) |
| **v0.10.1** | First-class albums | 🔜 Not Started | [details](v0.10.x.md#v0101-first-class-albums) |
| **v0.10.2** | Physical media & Discogs | 🔜 Not Started | [details](v0.10.x.md#v0102-physical-media--discogs) |
| **v0.10.3** | Manual scrobbling | 🔜 Not Started | [details](v0.10.x.md#v0103-manual-scrobbling) |
| **v1.0.0** | Multi-user auth & production polish | 🔜 Not Started | [details](v1.0.x.md#v100-multi-user-auth--production-polish) |
| **v1.1.0** | Privacy controls & public profiles | 🔜 Not Started | [details](v1.1.x.md#v110-privacy-controls--public-profiles) |
| **v1.1.1** | Social graph & follows | 🔜 Not Started | [details](v1.1.x.md#v111-social-graph--follows) |
| **v1.1.2** | Activity feed & social context | 🔜 Not Started | [details](v1.1.x.md#v112-activity-feed--social-context) |
| **v1.1.3** | Sharing & growth | 🔜 Not Started | [details](v1.1.x.md#v113-sharing--growth) |

---

## Persona Alignment

Each milestone maps to a persona from [docs/personas.md](../personas.md):

| Version | Primary Persona | Why |
|---------|----------------|-----|
| v0.4.x | Weekly Curator | Core workflow ritual — build, run, review playlists |
| v0.5.0 | Tinkerer | CI/CD safety net before irreversible infra changes |
| v0.5.1 | Tinkerer | PostgreSQL unlocks remote hosting + concurrency |
| v0.5.2 | Tinkerer | PostgreSQL-native polish — timeouts, constraints, query consolidation |
| v0.5.3 | Tinkerer | Docker + Fly.io — narada leaves the dev machine |
| v0.5.4 | Tinkerer | OAuth in the browser — deployed app is actually usable |
| v0.5.5 | Weekly Curator | Parallel workflows + caching — faster, snappier experience |
| v0.6.0 | Weekly Curator | Preference system — graduate binary likes into dislike/like/love; migrate 15k+ existing likes |
| v0.6.1 | Weekly Curator | Tagging system — freeform namespaced tags (mood:chill, energy:high) for categorization |
| v0.6.2 | Weekly Curator | Spotify playlist mapping — existing playlists as preference/tag import sources |
| v0.6.3 | Weekly Curator | Workflow integration + quick filters — preference/tag nodes, ad-hoc filtering, "save as workflow" |
| v0.7.0 | Weekly Curator | Data quality tools — fix mappings, find gaps, detect staleness. Useful now with existing connectors |
| v0.7.1 | Both | Apple Music broadens streaming coverage for Curator, broadens appeal for Tinkerer |
| v0.7.2 | Weekly Curator | Rekordbox brings owned-music metadata (BPM, key, codec, lossless) into the unified library |
| v0.8.0 | Weekly Curator | Scheduling automates the ritual — playlists and source data stay fresh without manual triggers |
| v0.8.1 | Both | Templates for Tinkerer onboarding, import/export for sharing, playlist browse for everyone |
| v0.9.0 | Casual Enthusiast | LLM creation is THE adoption feature — natural language → working playlist. Changes who can use narada. Templates from v0.8.1 inform prompt engineering. |
| v0.10.0 | Weekly Curator | Artist-level curation and identity resolution |
| v0.10.1 | Weekly Curator | Album-level browsing, identity resolution, cross-service album mapping |
| v0.10.2 | Weekly Curator | Physical media ownership — vinyl/CD/digital tracked via Discogs collection import |
| v0.10.3 | Weekly Curator | Manual scrobbling — log physical album listens to Last.fm + canonical play history |
| v1.0.0 | Tinkerer | Auth + per-user data isolation + security hardening for the hosted instance |
| v1.1.0 | Both | Privacy controls + public profiles — foundation for all social features, public playlists as discovery mechanism |
| v1.1.1 | Weekly Curator | One-way follows — subscribe to other curators' shared content, taste-based user discovery |
| v1.1.2 | Weekly Curator | Activity feed + social context — "Alex loved this track" on pages you're viewing |
| v1.1.3 | Casual Enthusiast | Shareable playlist links, embeds, invites — the viral growth mechanism |

---

## Infrastructure Readiness Matrix

Visual guide to infrastructure capabilities across version milestones:

| Capability | v0.2.7 (CLI) | v0.3.0 (Web Local) | v0.5.x (Deployed) | v1.0.0 (Multi-User) |
|------------|--------------|-------------------|-------------------|---------------------|
| **Testing** | ✅ pytest suite, <1min | ✅ + Vitest components | ✅ + E2E (Playwright) | ✅ Same |
| **CI/CD** | ⚠️ Manual | ⚠️ Manual | ✅ GitHub Actions | ✅ Same |
| **Deployment** | ✅ uv install | ✅ Local (SQLite) | ✅ Docker + Fly.io | ✅ Same |
| **Observability** | ✅ Loguru JSON logs | ✅ Same | ✅ Same | ✅ + Email alerts |
| **Authentication** | ❌ Not needed | ❌ Env var tokens | ✅ Spotify OAuth | ✅ + Email/password |
| **Database** | ✅ SQLite | ✅ PostgreSQL (Docker) | ✅ PostgreSQL | ✅ PostgreSQL |
| **Caching** | ❌ Not needed | ✅ Tanstack Query | ✅ + lru_cache | ✅ Same |
| **Security** | ✅ Env vars, secrets | ✅ + CORS (localhost) | ✅ + HTTPS | ✅ + bcrypt |

**Legend**: ✅ Ready | ⚠️ Needs work | ❌ Not needed

**Note**: Right-sized for a local music community (dozens to low hundreds of users). No Redis, CDN, MFA, load testing, or enterprise observability. Focus on quality code over production infrastructure. Revisit scaling if usage exceeds 100+ active users.

---

## Technology Decision Records

Key architecture & tech choices (see CLAUDE.md for migration details):

- **Python 3.14+ & attrs**: Modern type syntax (`str | None`, `class Foo[T]`), immutable domain entities with slots
- **PostgreSQL (v0.5.1)**: Migrated from SQLite for remote hosting and parallel Prefect execution. `psycopg3` driver (SQLAlchemy 2.1 default), managed hosting via Neon (free tier). SQLite removed entirely — PostgreSQL-only. Repository pattern meant zero application-layer code changes. Post-migration optimization: all JSON→JSONB, pg_trgm trigram search, BRIN indexes, DB-side aggregations, tuple IN queries.
- **Vite 8 / Vitest**: Rolldown-powered unified bundler, 10-30x faster builds, native ESM + TypeScript
- **Tailwind CSS v4**: Rust engine (10x performance), @theme design tokens
- **Pydantic v2**: 5-50x faster validation, `from_attributes=True`
- **Clean Architecture + DDD**: Composable workflows, isolated APIs, testable logic (see docs/architecture/README.md)

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
