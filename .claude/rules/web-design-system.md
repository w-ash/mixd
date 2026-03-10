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

## Accessibility (WCAG 2.2 AA)
- Semantic HTML: `nav`, `main`, `article`, `header` — not div soup
- ARIA labels on all interactive elements; `aria-live` for dynamic progress updates
- Keyboard navigation: Tab order, Esc closes modals, Enter submits
- 44x44px minimum touch targets; 4.5:1 contrast ratio for text

## Anti-AI-Slop Design Principles

All LLMs train on the same layouts and templates — without specific guidance, they converge on the statistical average of "website." These rules exist to prevent that.

### Structural Rules
- **Every element must be defensible** — if you can't justify its existence and placement, remove it
- **Design all states** — empty, loading, error, single item, overflow, long text. Never just the happy path.
- **Test with real content** — use actual track titles, artist names, and play counts. Not "Song Title" x 5.
- **No Frankenstein layouts** — sections must flow with narrative purpose, not feel randomly assembled

### Visual Identity (Narada-specific)
- **3-level depth system** (inset/flat/elevated) — no uniform containers. If every card looks the same, hierarchy is broken.
- **No native `<select>`** — always Radix Select. Native dropdowns break dark theme.
- **Entrance animations** — page content fades up on route change. First-load lists stagger.
- **Background grain** — noise texture overlay always present. Breaks flat digital monotony.
- **Asymmetric borders** — prefer left-accent bars and bottom rules over full border boxes.

### Visual Clichés to Avoid
- Purple/blue/indigo gradients as primary palette (the AI default — every LLM reaches for `bg-indigo-500`)
- Glassmorphism as design foundation (surgical accent on one element is fine — as the whole theme, never)
- Blobby decorative background shapes that serve no purpose
- Every container with identical `rounded-xl border bg-card p-4`
- Uniform spacing between all sections (vary the rhythm)
- `animate-pulse` skeleton loaders (use shimmer gradient instead)
- Text-only empty states without visual presence
- Static pages with no entrance animation
- Native browser form controls in a dark theme
- Over-generating elements — more UI isn't better UI. Reduce cognitive load.
