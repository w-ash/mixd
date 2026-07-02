# NN — <Title>

> Spoke of [The Fable Sweep](README.md) ([v0.8.12](../v0.8.12.md)). Self-contained work order — written so a fresh agent can execute it cold, without re-reading the whole codebase.

**Area:** \<domain | application | infrastructure | interface | web> · **Suggested executor:** \<Fable | Opus | Haiku> · **Effort:** \<XS–XXL> · **ROI:** \<high | med | low> · **Risk:** \<high | med | low> · **Status:** Not Started

## Problem

<What's wrong, with **file paths + line counts + concrete evidence** — quote the duplicated blocks or the oversized function. Specific enough that the executor doesn't re-derive it.>

## Why it matters

<To the user (indirect: reliability/velocity, or a **direct** UX/perf win) and to the maintainer. If this changes user-visible behavior, say so explicitly and name the affected `docs/web-ui/01-user-flows.md` flow.>

## Proposed change

<Precise: what merges into what, the new module layout / extraction seams, the target shape. Name the real identifiers/paths — this is the locked-anchor section.>

## Blast radius & behavior-preservation

<Every call site / import touched. The argument for why behavior is unchanged — or, for a UX/perf spoke, the before/after and why it's net-positive.>

## Test plan

<Existing tests that guard this (named files/cases) + any characterization test to add *first*. Reuse `make_track` / `make_mock_uow`. Web: name the `*.test.tsx` files.>

## Guardrails (do not skip)

- **Clean break:** no shims/aliases/re-export layers; one import path per thing; update **every** call site.
- **Grep gate:** `git grep '<removed_symbol>'` returns nothing when done.
- **Layer flow:** inward-only; domain stays `@define(frozen=True, slots=True)` + pure; the `interface/`→infra OAuth exception aside.
- **Green:** `uv run pytest` (+ `pnpm --prefix web test` if web) stays green; no test weakened to pass.
- **Ratchet:** if a suppressed ruff `PLR`/basedpyright rule now passes for this code, re-enable it here (no new `# noqa`).
- **Scope discipline:** unrelated debt → log in the hub's *Deferred* section, don't fix it here.

## Notes / counter-proposal

<Optional: a better approach than the one above, or dependencies on other spokes (by ID).>
