---
paths:
  - "web/src/components/**"
  - "web/src/pages/**"
---
# Web Design System — Dark Editorial Music Aesthetic

Narada is a power tool for music metadata enthusiasts — the "record store crate-digger," not the casual listener. The UI should feel precise, data-rich, and intentionally crafted. Every design choice must be defensible: if you can't explain why an element exists and why it's positioned there, cut it.

## Styling
- Tailwind v4 with `@theme` tokens in `theme.css` — CSS variables are the single source of truth
- CSS-first animations (150ms interactive, 300ms layout); Motion library only for orchestrated sequences
- Dark mode is default — never assume light backgrounds

## Typography Hierarchy (enforce)
- Display font (Space Grotesk): headings, buttons, nav labels, section titles
- Body font (Newsreader): descriptions, prose, metadata values
- Mono font (JetBrains Mono): ISRCs, IDs, durations, timestamps, code
- Never use Inter, Roboto, system-ui for display text

## Accessibility: WCAG 2.2 AA
- 44×44px minimum touch targets; 4.5:1 contrast ratio for text
- `aria-live` for dynamic progress updates (sync status, import progress)

## Self-Explanatory Interface Principles

### Status Indicators
- **Never color alone** — every status must combine at least two of: icon, color, text label. A colored dot without a label is meaningless.
- **Use contextual text** — "Synced 2h ago", "Sync failed: rate limited", "Strong match (85%)" — not just "green dot" or bare percentages.
- **Reuse `StatusIndicator` pattern** — consistent component across playlist sync status, connector status, and match quality.

### Confirmation & Destructive Actions
- **Confirmation dialogs for serious consequences only** — don't cry wolf. Routine actions need no confirmation. Sync operations that modify external services always do.
- **Restate what will happen** — "This will add 12 tracks to Summer Vibes on Spotify" not "Are you sure?"
- **Action-specific button labels** — "Sync to Spotify" not "Confirm". "Remove link" not "Yes".
- **Default focus on the safe option** (Cancel), not the destructive action.

### Discoverable Actions
- **Never hide primary actions behind hover** — `opacity-0 group-hover:opacity-100` is forbidden for important actions. Use subtle-at-rest styling instead (muted color that strengthens on hover).
- **Buttons describe consequences** — "Sync to Spotify", "Import from Last.fm", not generic "Run" or "Sync".
- **Inline consequence hints** for destructive actions — show what will happen before the user opens a dialog.

### Microcopy
- **Titles must be comprehensible standalone** — users scan, they don't read. If the title doesn't make sense without the body, rewrite it.
- **Plain language over jargon** — "Local → Spotify" not "push". "Spotify Data Export" not "GDPR Export". "Since your last import" not "incremental".
- **Explain the "why" before the "what"** — "To see your listening stats, connect Last.fm" not just "Connect Last.fm".

### Progressive Disclosure
- **Basics visible, details expandable** — show the essential information at rest, let users expand for more.
- **Described options** — radio buttons and selectors include short descriptions, not just labels.
- **Error details expandable** — show summary at rest, full error on click/expand.

### Consistency Across Pages
- **Same action = same pattern everywhere** — connector relationships (track mappings, playlist links) use the same visual component, button style, and interaction pattern regardless of page.
- **Same status = same indicator everywhere** — sync status, match quality, and connection status all use the same `StatusIndicator` system.

## Anti-AI-Slop Design Principles

All LLMs train on the same layouts and templates — without specific guidance, they converge on the statistical average of "website." These rules exist to prevent that.

### Visual Identity (Narada-specific)
- **3-level depth system** (inset/flat/elevated) — no uniform containers. If every card looks the same, hierarchy is broken.
- **No native `<select>`** — always Radix Select. Native dropdowns break dark theme.
- **Entrance animations** — page content fades up on route change. First-load lists stagger.
- **Background grain** — noise texture overlay always present. Breaks flat digital monotony.
- **Asymmetric borders** — prefer left-accent bars and bottom rules over full border boxes.

### Visual Clichés to Avoid
- Purple/blue/indigo gradients as primary palette (the AI default)
- Glassmorphism as design foundation (surgical accent fine — never as whole theme)
- Every container with identical `rounded-xl border bg-card p-4` — vary depth
- Uniform spacing between all sections (vary the rhythm)
- `animate-pulse` skeleton loaders (use shimmer gradient instead)
- Text-only empty states without visual presence
- Native browser form controls in a dark theme
