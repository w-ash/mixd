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
- **Sidebar masthead**: centered `h-28` block, 48px record above wide-tracked uppercase "NARADA" in `text-text-muted`
- 3-level depth (inset/flat/elevated); asymmetric borders (left-accent bars over full border boxes)
- No native `<select>` (use Radix); entrance animations on route change; background grain texture
- **Avoid**: indigo/blue/purple gradients, glassmorphism as foundation, uniform `rounded-xl border bg-card p-4`, `animate-pulse` skeletons (use shimmer), text-only empty states, native form controls
