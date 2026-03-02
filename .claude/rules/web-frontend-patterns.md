---
paths:
  - "web/**"
---
# Web Frontend Rules (React + TypeScript + Tailwind v4)

## Component Architecture
- **Three layers**: `ui/` (shadcn/ui Radix primitives), `shared/` (narada composites reused across pages), `pages/` (route-level, own data fetching via Tanstack Query)
- shadcn/ui is owned source in `web/src/components/ui/` — customize freely to dark editorial aesthetic
- Keep components small. Extract to `shared/` only when reused across 2+ pages.

## State Management (No Redux/Zustand)
- **Server state**: Tanstack Query v5 — hooks in `web/src/api/*.ts`, stale-while-revalidate, background refetch
- **URL state**: React Router search params for filters, pagination, search
- **UI state**: React `useState` / `useReducer` for modals, forms, local interactions
- **Operation progress**: `useSSE` hook + Tanstack Query for real-time operation tracking
- If cross-page state emerges, evaluate React Context before reaching for a library

## TypeScript & API Integration
- **Strict mode** always — no `any`, no `@ts-ignore`
- API hooks export `useQuery`/`useMutation` from `web/src/api/*.ts`
- Error handling: interceptor converts API error envelope `{error: {code, message, details}}` to typed `ApiError`
- Types generated from FastAPI OpenAPI schema in `web/src/types/api.ts`

## Styling
- Tailwind v4 with `@theme` tokens in `theme.css` — CSS variables are the single source of truth
- CSS-first animations (150ms interactive, 300ms layout); Motion library only for orchestrated sequences
- Dark mode is default — never assume light backgrounds

## Accessibility (WCAG 2.2 AA)
- Semantic HTML: `nav`, `main`, `article`, `header` — not div soup
- ARIA labels on all interactive elements; `aria-live` for dynamic progress updates
- Keyboard navigation: Tab order, Esc closes modals, Enter submits
- 44x44px minimum touch targets; 4.5:1 contrast ratio for text
