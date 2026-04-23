---
paths:
  - "web/src/components/**"
  - "web/src/pages/**"
---
# Web Design System — Dark Editorial Music Aesthetic

Power tool for music metadata enthusiasts. Every element must be defensible.

## Styling

- Tailwind v4 + `@theme` tokens in `theme.css` — CSS variables are the single source of truth.
- CSS-first animations (150ms interactive, 300ms layout); Motion library only for orchestrated sequences.
- Dark mode default; support light mode via user preference.

## Typography

- Display (Space Grotesk): headings, buttons, nav labels, section titles.
- Body (Newsreader): descriptions, prose, metadata values.
- Mono (JetBrains Mono): ISRCs, IDs, durations, timestamps, code.

## Spacing — 8-point editorial rhythm

Every spacing value comes from this scale.

| Level | Tailwind | px | Use |
|---|---|---|---|
| Tight | `space-y-1`, `gap-1` | 4 | Label→value coupling, subtitle under title |
| Dense | `space-y-2`, `gap-2` | 8 | List items, inline badges, icon+text |
| Compact | `space-y-3`, `gap-3` | 12 | Cards in a list, skeleton items, filter controls |
| Standard | `gap-4`, `space-y-4` | 16 | Grid gaps, form field groups |
| Relaxed | `space-y-6`, `mb-6` | 24 | Content below header, filter bar→table |
| Section | `space-y-8`, `mt-8` | 32 | Between major page sections |
| Category | `space-y-12` | 48 | Settings-style large category separation |

**Card padding**: content cards (stat, operation, connector, section panels) → `p-5`; list-item rows → `px-4 py-3`; dialog callouts and editor panels → `p-4`.

**Metadata fields**: `gap-x-6 gap-y-2` for all `flex-wrap` metadata layouts.

**Page structure**: `px-page py-8` (PageLayout), `mb-8` after PageHeader, `mt-8` between sections, `mt-6` for pagination below tables.

**Out of scale** (use the next allowed value): `mt-10`, `space-y-10`, `gap-x-8` for metadata, `p-4` on standalone content cards.

## Accessibility — WCAG 2.2 AA

44×44px touch targets; 4.5:1 contrast; `aria-live` for progress updates.

## Self-Explanatory Interface

- **Status**: icon + color + text label (color alone is insufficient). Contextual: "Synced 2h ago" not "Synced".
- **Confirmations**: serious consequences only. Title restates action. Action-specific labels. Default focus on Cancel.
- **Actions**: always-visible with muted-at-rest styling. Labels describe consequences ("Import from Last.fm", not "Run").
- **Microcopy**: titles comprehensible standalone. Plain language. Lead with "why".
- **Progressive disclosure**: basics visible, details expandable. Selectors include descriptions.

## Cross-Page Consistency

- Same pattern on 2+ pages → shared component. Same action/status = same component everywhere.
- **Existing primitives**: `ui/` (Button, Card, Dialog, Select, Input, Badge, Table, Skeleton, Switch); `shared/` (EmptyState, StatusIndicator, ConfirmationDialog, SyncConfirmationDialog, SectionHeader, OperationProgress, ConnectorCard, RunStatusBadge, NodeTypeBadge, TablePagination).
- **Four states for every data view**: loading (Skeleton), empty (EmptyState), error (boundary), success.
- Sibling cards share radius/shadow/padding. Vary depth between hierarchy levels, not within.

## Visual Identity — Anti-AI-Slop

- **Golden record** mark — `#C59A2B` disc. Gold palette: warm gold (`oklch(0.75 0.15 85)`) primary accent.
- **Sidebar masthead**: centered `h-28` block, 48px record above wide-tracked uppercase "MIXD" in `text-text-muted`.
- 3-level depth (inset/flat/elevated); asymmetric borders (left-accent bars on section containers, not standalone text labels).
- Use Radix instead of native `<select>`; entrance animations on route change; background grain texture.
- **Stay away from**: indigo/blue/purple gradients, glassmorphism as foundation, uniform `rounded-xl border bg-card p-4`, `animate-pulse` skeletons (use shimmer), text-only empty states, native form controls.
