# 20 — Design-token conformance: SyncConfirmationDialog raw palette

> Spoke of [The Fable Sweep](README.md) ([v0.8.12](../v0.8.12.md)). Self-contained work order — written so a fresh agent can execute it cold, without re-reading the whole codebase.

**Area:** web · **Suggested executor:** Haiku · **Effort:** XS-S · **ROI:** low-med · **Risk:** low · **Status:** Not Started

## Problem

`components/shared/SyncConfirmationDialog.tsx` uses raw Tailwind palette classes instead of the design system's semantic tokens: `text-green-500`, `text-red-500/400`, `text-amber-400`, `text-blue-400`, `bg-red-500/10` at lines 56, 64, 105, 290, 301, 308, 325 — the design system mandates `text-status-*` / `destructive` tokens. Lighter instances: `SSELivenessPill.tsx:31-56`, `PreferenceToggle.tsx:19-31`.

## Why it matters

User: **direct but subtle** — raw palette colors don't respond to theme adjustments, so this dialog can drift from the app's dark-editorial look (frontend-design skill). Maintainer: token discipline is only as strong as its weakest screen.

## Proposed change

Map each raw class to its semantic token per the frontend-design skill (read it first): success/error/warning/info statuses → `text-status-*`; destructive actions → `destructive` token; verify in both the default theme and any contrast variants. Fix all three files.

## Blast radius & behavior-preservation

Visual-only; colors should render identically or closer-to-spec. This is a flagged **net-positive UX conformance** change (colors may shift a shade toward the token values) — no `01-user-flows.md` re-true needed (no flow semantics change), but include before/after screenshots of the sync dialog in the PR.

## Test plan

`pnpm --prefix web test src/components/shared/SyncConfirmationDialog.test.tsx` (+ pill/toggle tests) — class-name assertions may need re-pointing to tokens (that's re-truing, not weakening). `pnpm --prefix web check` for Biome.

## Guardrails (do not skip)

- **Grep gate:** `git grep -nE 'text-(green|red|amber|blue)-[0-9]' web/src/components/shared/SyncConfirmationDialog.tsx web/src/components/shared/SSELivenessPill.tsx web/src/components/playlist/PreferenceToggle.tsx` returns nothing when done.
- **Green:** `pnpm --prefix web test` + `pnpm --prefix web check` stay green.
- **Scope discipline:** these three files only; a repo-wide raw-palette sweep is a different (bigger) decision — if more instances surface, log them in the hub.

## Notes / counter-proposal

None.
