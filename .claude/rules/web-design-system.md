---
paths:
  - "web/src/components/**"
  - "web/src/pages/**"
---
# Web Design System — Dark Editorial Music Aesthetic

Power tool for music metadata enthusiasts. Precise, data-rich, intentionally crafted. Every element must be defensible — if you can't explain why it exists, cut it.

## Styling
- Tailwind v4 + `@theme` tokens in `theme.css` — CSS variables are single source of truth
- CSS-first animations (150ms interactive, 300ms layout); Motion library only for orchestrated sequences
- Dark mode default — never assume light backgrounds

## Typography (enforce)
- Display (Space Grotesk): headings, buttons, nav labels, section titles
- Body (Newsreader): descriptions, prose, metadata values
- Mono (JetBrains Mono): ISRCs, IDs, durations, timestamps, code
- Display text uses only Space Grotesk, Newsreader, or JetBrains Mono

## Accessibility: WCAG 2.2 AA
- 44×44px touch targets; 4.5:1 contrast; `aria-live` for progress updates

## Self-Explanatory Interface

### Status — never color alone
- Combine icon + color + text label. Bare colored dots are meaningless.
- Contextual detail: "Synced 2h ago" not just "Synced". "Strong match (85%)" not bare numbers.

### Confirmations — don't cry wolf
- Only for serious consequences (external service mutations, data deletion). Not routine actions.
- Title restates what will happen ("Add 12 tracks to Summer Vibes on Spotify"), not "Are you sure?"
- Action-specific labels ("Sync to Spotify", "Remove link") — never "Yes"/"OK"/"Confirm".
- Default focus on Cancel.

### Discoverable Actions
- Always-visible actions with muted-at-rest styling (no `opacity-0 group-hover:opacity-100`).
- Buttons describe consequences: "Import from Last.fm" not "Run".

### Microcopy
- Titles comprehensible standalone — users scan, not read.
- Plain language: "Local → Spotify" not "push". "Since your last import" not "incremental".
- Lead with "why": "To see listening stats, connect Last.fm" not just "Connect Last.fm".

### Progressive Disclosure
- Basics visible, details expandable. Error details: summary at rest, full on expand.
- Radio buttons/selectors include descriptions, not just labels.

### Cross-Page Consistency
- **Same pattern on 2+ pages → shared component.** Never duplicate inline class strings.
- Same action/status = same component everywhere. All status uses `StatusIndicator`.
- **Check existing primitives first:**
  - `ui/`: `Button`, `Card`, `Dialog`, `Select`, `Input`, `Badge`, `Table`, `Skeleton`, `Switch`
  - `shared/`: `EmptyState`, `StatusIndicator`, `ConfirmationDialog`, `SyncConfirmationDialog`, `SectionHeader`, `OperationProgress`, `ConnectorCard`, `RunStatusBadge`, `NodeTypeBadge`, `TablePagination`
- **Four states for every data view** — loading (`Skeleton`), empty (`EmptyState` with explain + suggest + action), error (boundary), success. Design all four before shipping.
- Sibling cards share radius/shadow/padding. Vary depth between hierarchy levels, not within.

## Anti-AI-Slop — Narada Visual Identity

### Brand
- **Golden record** mark — `#C59A2B` disc, `#D4AC35` label, `#9E7B1F` rim. Favicon + sidebar masthead.
- **Sidebar masthead** — centered `h-28` block: 48px record above wide-tracked uppercase "NARADA" in `text-text-muted`. Magazine masthead treatment.
- **Gold palette** — warm gold (`oklch(0.75 0.15 85)`) primary accent. Three shades for depth.

### Signature Elements
- 3-level depth (inset/flat/elevated) — uniform containers = broken hierarchy
- No native `<select>` — always Radix. Native dropdowns break dark theme.
- Entrance animations on route change; staggered first-load lists
- Background grain texture overlay always present
- Asymmetric borders — left-accent bars over full border boxes

### Avoid
- Indigo/blue/purple gradients (the AI default palette)
- Glassmorphism as foundation (surgical accent only)
- Identical `rounded-xl border bg-card p-4` on every container — vary depth
- Uniform spacing — vary the rhythm
- `animate-pulse` skeletons (use shimmer gradient)
- Text-only empty states; native browser form controls in dark theme
