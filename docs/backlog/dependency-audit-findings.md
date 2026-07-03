# Dependency Audit — Findings

**Status**: Active — work orders W1–W10 unscheduled.

> Docs-first pass from the [v0.8.17 sweep closeout](v0.8.13-0.8.17.md#v0817-sweep-closeout--ratchet--review) (2026-07-02, branch `sweep-closeout-v0.8.17`), sibling to the v0.8.12 code audit: every dependency **necessary**, on the **best-supported option** for its job, and **fully utilized** rather than shadowed by bespoke code. No runtime changes ship with this doc — each actionable finding is a work order at the bottom, scheduled separately.

**Scope**: `pyproject.toml` (21 runtime + 12 dev), `web/package.json` (21 runtime + 20 dev), root `package.json` (1 dev). Tools: `uvx deptry .`, `pnpm --prefix web lint:dead` (knip), `uv pip list --outdated`, `pnpm outdated`, targeted usage greps.

**Verdict in one line**: the tree is healthy — zero unused runtime deps on either side, versions current to within patch/minor releases — with two unwired dev tools, one undeclared transitive in production code, and a handful of utilization/posture gaps, all small.

## Lens 1 — Necessity (is it used at all?)

**Backend runtime (21/21 used).** deptry flags three `DEP002` "unused" that are all module-name false positives, paired with their own `DEP001` counterparts: `python-dotenv` → imported as `dotenv` (`src/config/settings.py:24`), `pyjwt` → imported as `jwt` (`src/interface/api/auth_gate.py:19`), `python-multipart` → never imported by us at all, but FastAPI requires it at runtime for `UploadFile`/form parsing (`src/interface/api/routes/imports.py`). A `[tool.deptry.package_module_name_map]` would silence all three if deptry is adopted (→ W7).

**The one real deptry hit — `starlette` is imported but undeclared.** Three ASGI middleware modules import it directly (`auth_gate.py`, `caching.py`, `security_headers.py` — `starlette.datastructures` + `starlette.types`; FastAPI does not re-export these). We depend on it only transitively via `fastapi`, so a future fastapi bump can move starlette under our feet with no resolver signal. Declare it explicitly (→ W1).

**Backend dev (10/12 wired, 2 dead).**

| Dep | Disposition |
|---|---|
| pytest, pytest-asyncio, pytest-cov, pytest-xdist | Wired (suite runs `-n` distributed, CI runs `--cov`) |
| ruff, basedpyright, vulture, pre-commit | Wired (CI + `.pre-commit-config.yaml` + `check_ratchet.sh`) |
| testcontainers | Wired (integration suite PostgreSQL) |
| psutil | Wired, barely — one perf test (`tests/integration/test_large_playlist_performance.py`); keep, document |
| **interrogate** | **Unwired** — no `[tool.interrogate]`, no CI step, no pre-commit hook, no docs mention. Dead install (→ W2) |
| **bandit** | **Unwired** — same; ruff's `S` ruleset (flake8-bandit) is enabled and covers the lint surface. The stray `# nosec B311` in `metadata_transforms/shuffle.py:91` is a fossil of a manual bandit era (→ W2) |

**Web (41/41 used).** knip exits clean — no unused dependencies, exports, or files. Root `package.json`: `concurrently` drives the `pnpm dev` orchestration line. All necessary.

## Lens 2 — Modernity & support

**Backend**: everything current. `uv pip list --outdated` shows only patch/minor lag: coverage 7.14.3→7.15.0, croniter 6.2.2→6.2.3, pydantic-core 2.46.4→2.47.0, stevedore 5.8.0→5.9.0, typing-extensions 4.15.0→4.16.0 — all transitive or trivial, none urgent. Core stack (SQLAlchemy 2.0.51, FastAPI 0.139, Starlette 1.3.1, pydantic 2.13.4, structlog 26.1) is the maintained mainline for each job; no dep is superseded or abandoned.

**Web**: `pnpm outdated` shows only `@biomejs/biome` 2.5.1→2.5.2 and `lucide-react` 1.22→1.23. Current.

**Forward signals (watch items, carried from the v0.8.14/15 bumps + this closeout):**

1. **Starlette 1.3 deprecates `httpx` under its TestClient in favour of `httpx2`** — our full suite emits 15 `StarletteDeprecationWarning`s today via `fastapi.testclient`. Production connector code on `httpx` 0.28 is unaffected (the deprecation is TestClient-scoped). Plan the test-client migration before Starlette hard-removes it (→ W4).
2. **`@neondatabase/auth` is `0.4.2-beta`** (latest published) and drags ~95 lockfile entries of deprecated `@react-email/*` transitives (auth email templates). The pathway is still the right one — it's the vendor SDK for Neon Auth — but it's a beta pinned exactly; track GA and re-audit the transitive tail at that bump (→ W8).
3. **Playwright browser↔baseline coupling** (proven this closeout, see supply-chain below): the CI container tag must equal `@playwright/test`'s version or the e2e gate dies at browser launch — and every Playwright bump requires regenerating visual baselines in the new image. Bump package + CI image + baselines as one unit (procedure in `web/e2e/README.md`; now also on CLAUDE.md's version-bump bar).
4. **PyPI name collision**: `uv pip list --outdated` reports our local `mixd 0.8.16` as "outdated" vs an unrelated **`mixd 1.2` on PyPI**. Harmless today (we never install from PyPI), but it's a latent dependency-confusion footgun for any future `pip install mixd` in CI or docs (→ W9).

## Lens 3 — Utilization vs bespoke (are we reimplementing what a dep gives us?)

- **tenacity** — properly used for connector retry/backoff (`base.py` + 3 connector clients). **One hand-roll found**: `base_repo.py` catches "concurrent operations are not permitted" and retries with a bare `await asyncio.sleep(0.1)` loop — tenacity-shaped logic without tenacity's jitter/limits/logging (→ W3). The other `asyncio.sleep` sites audited are honest non-retries: the MusicBrainz 1 req/s throttle (rate-limit policy, not retry), poll/progress loops (`periodic_loop.py`, SSE/progress code).
- **rapidfuzz / jellyfish** — each used exactly where it should be (`matching/algorithms.py` fuzzy scoring; `text_normalization.py` phonetics). No bespoke string-distance code found; the pair is complementary, not redundant.
- **croniter** — correctly caged: one import (`schedule_timing.py`), the v0.8.2 decision (internal DST-correct next-occurrence math only, no user-facing cron) holds.
- **orjson** — wired as the psycopg JSONB serializer (`db_connection.py`, deliberate, documented in-module). **Not** wired as FastAPI's response class — API responses still serialize via stdlib json. Optional cheap win if any list endpoint gets hot (→ W6, benchmark first; payloads are modest today).
- **pydantic** — API schemas + settings only, as designed; domain stays attrs-frozen. No bespoke validation shadowing it found.

## Supply-chain posture (documented, not fought)

- **Security-floor overrides** live in **`web/pnpm-workspace.yaml`** (not the repo root — the v0.8.17 spec's root-path reference was off by a directory): `hono >=4.12.7`, `fastify >=5.8.3`, `defu >=6.1.5`, `picomatch >=4.0.4`, migrated from `package.json` `pnpm.overrides` for pnpm 11.5+.
- **pnpm 11.5's lockfile supply-chain verification is active** — observed during this closeout's container installs: "Verifying lockfile against supply-chain policies (890 entries)". The release-age cooldown that deferred four <24h-old releases at the v0.8.15 bump is pnpm's built-in policy behavior, **not** an explicit repo setting — `pnpm config get minimumReleaseAge` is unset. Making it declarative in `pnpm-workspace.yaml` would pin the posture against pnpm default changes (→ W5).
- **Playwright image pinning** is part of this posture: the CI container (`mcr.microsoft.com/playwright:v1.61.1-noble`) is the only sanctioned browser build for visual baselines; treat its tag as a locked dependency coupled to `@playwright/test` (see Lens 2, signal 3).

## Work orders

Each schedules independently (revision or later version); none gate the v0.8.17 ship.

- **W1 (XS)** — Declare `starlette` explicitly in `pyproject.toml` runtime deps (already installed transitively; this is a one-line honesty fix so resolver + audits see the real dependency). Constraint: keep the floor aligned with fastapi's requirement.
- **W2 (XS, user sign-off)** — Remove `interrogate` and `bandit` from dev deps (both unwired; ruff `S` covers bandit's lint surface). Drop the fossil `# nosec B311` in `shuffle.py` in the same commit.
- **W3 (S)** — Replace `base_repo.py`'s hand-rolled concurrent-session retry loop with tenacity (same policy the connector clients use); regression test at repository layer.
- **W4 (S, blocked on upstream)** — Migrate test client to `httpx2` when Starlette schedules the hard removal; until then the 15 deprecation warnings are the tracked signal (do not filter them out).
- **W5 (XS)** — Add explicit `minimumReleaseAge` (24h, matching current behavior) to `web/pnpm-workspace.yaml` so the cooldown is repo policy, not tool default.
- **W6 (XS-S, optional)** — Benchmark `ORJSONResponse` as FastAPI `default_response_class`; adopt only if a hot list endpoint shows it.
- **W7 (S, optional)** — Adopt deptry in CI (`uvx deptry .` step + `[tool.deptry.package_module_name_map]` for dotenv/jwt/multipart) so undeclared-import regressions like W1 are caught structurally.
- **W8 (watch)** — `@neondatabase/auth`: bump at GA, re-audit the `@react-email/*` transitive tail then.
- **W9 (XS, docs)** — Note the PyPI `mixd` name collision in `docs/deployment.md` (never `pip install mixd`; the package is uv-workspace-local only).
- **W10 (S-M, from spoke 26)** — **import-linter**: v0.8.12 claimed layer enforcement was "already in place"; it is not installed anywhere (no config, not in uv.lock, CI, or pre-commit). Install it, encode the layer contract (domain ← application ← interface/infrastructure) with `ignore_imports` for the sanctioned interface→infrastructure OAuth carve-out, and add it to the CI Python job — closing the gap between the documented architecture and what's mechanically enforced.
