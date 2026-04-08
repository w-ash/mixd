---
paths:
  - "web/src/components/**"
  - "web/src/pages/**"
---
# Web Design System — Dark Editorial Music Aesthetic

Power tool for music metadata enthusiasts. Every element must be defensible.

## Styling
- Tailwind v4 + `@theme` tokens in `theme.css` — CSS variables are single source of truth
- CSS-first animations (150ms interactive, 300ms layout); Motion library only for orchestrated sequences
- Dark mode default; support light mode via user preference — never assume light backgrounds

## Typography (enforce)
- Display (Space Grotesk): headings, buttons, nav labels, section titles
- Body (Newsreader): descriptions, prose, metadata values
- Mono (JetBrains Mono): ISRCs, IDs, durations, timestamps, code

## Spacing (8-point editorial rhythm)
Every spacing value must come from this scale. No ad-hoc values.

| Level | Tailwind | px | Use |
|-------|----------|-----|-----|
| Tight | `space-y-1`, `gap-1` | 4 | Label→value coupling, subtitle under title |
| Dense | `space-y-2`, `gap-2` | 8 | List items, inline badges, icon+text |
| Compact | `space-y-3`, `gap-3` | 12 | Cards in a list, skeleton items, filter controls |
| Standard | `gap-4`, `space-y-4` | 16 | Grid gaps, form field groups |
| Relaxed | `space-y-6`, `mb-6` | 24 | Content below header, filter bar→table |
| Section | `space-y-8`, `mt-8` | 32 | Between major page sections |
| Category | `space-y-12` | 48 | Settings-style large category separation |

### Card padding
- **Content cards** (stat cards, operation cards, connector cards, section panels): `p-5`
- **List-item rows** (connector items, node execution rows): `px-4 py-3`
- Dialog callouts and editor panels stay `p-4` (inline/contextual)

### Metadata fields
- Uniform: `gap-x-6 gap-y-2` for all `flex-wrap` metadata layouts

### Page structure
- Page frame: `px-page py-8` (PageLayout) — do not change
- After PageHeader: `mb-8` (built into PageHeader) — do not change
- Section separators within a detail page: `mt-8` (Section level)
- Pagination below tables: `mt-6` (Relaxed level)

### Forbidden values
- `mt-10` / `space-y-10` — not in scale (use `mt-8` or `space-y-12`)
- `gap-x-8` for metadata — too wide (use `gap-x-6`)
- `p-4` on standalone content cards — too tight (use `p-5`)

## Accessibility: WCAG 2.2 AA
- 44×44px touch targets; 4.5:1 contrast; `aria-live` for progress updates

## Self-Explanatory Interface
- **Status**: icon + color + text label (never color alone). Contextual: "Synced 2h ago" not "Synced"
- **Confirmations**: serious consequences only. Title restates action. Action-specific labels. Default focus on Cancel
- **Actions**: always-visible with muted-at-rest styling. Labels describe consequences: "Import from Last.fm" not "Run"
- **Microcopy**: titles comprehensible standalone. Plain language. Lead with "why"
- **Progressive disclosure**: basics visible, details expandable. Selectors include descriptions

## Cross-Page Consistency
- Same pattern on 2+ pages → shared component. Same action/status = same component everywhere
- **Check existing primitives**: `ui/` (Button, Card, Dialog, Select, Input, Badge, Table, Skeleton, Switch) and `shared/` (EmptyState, StatusIndicator, ConfirmationDialog, SyncConfirmationDialog, SectionHeader, OperationProgress, ConnectorCard, RunStatusBadge, NodeTypeBadge, TablePagination)
- **Four states for every data view**: loading (Skeleton), empty (EmptyState), error (boundary), success
- Sibling cards share radius/shadow/padding. Vary depth between hierarchy levels, not within

## Anti-AI-Slop — Visual Identity
- **Golden record** mark — `#C59A2B` disc. Gold palette: warm gold (`oklch(0.75 0.15 85)`) primary accent
- **Sidebar masthead**: centered `h-28` block, 48px record above wide-tracked uppercase "MIXD" in `text-text-muted`
- 3-level depth (inset/flat/elevated); asymmetric borders (left-accent bars on section containers, not standalone text labels)
- No native `<select>` (use Radix); entrance animations on route change; background grain texture
- **Avoid**: indigo/blue/purple gradients, glassmorphism as foundation, uniform `rounded-xl border bg-card p-4`, `animate-pulse` skeletons (use shimmer), text-only empty states, native form controls
