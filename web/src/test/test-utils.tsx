import {
  MutationCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { type RenderOptions, render } from "@testing-library/react";
import { type ReactElement, type ReactNode, useState } from "react";
import { MemoryRouter, type MemoryRouterProps } from "react-router";

import { ThemeProvider } from "#/contexts/ThemeContext";
import { WorkflowExecutionProvider } from "#/contexts/WorkflowExecutionContext";
import { createMutationErrorHandler } from "#/lib/toasts";

interface ProvidersProps {
  children: ReactNode;
  routerProps?: MemoryRouterProps;
}

export function createTestQueryClient() {
  return new QueryClient({
    // Mirror production so tests exercise the global error-toast handler.
    mutationCache: new MutationCache({
      onError: createMutationErrorHandler(),
    }),
    defaultOptions: {
      queries: {
        retry: false,
        gcTime: 0,
      },
      mutations: {
        retry: false,
      },
    },
  });
}

function Providers({ children, routerProps }: ProvidersProps) {
  const [queryClient] = useState(createTestQueryClient);
  return (
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <WorkflowExecutionProvider>
          <MemoryRouter {...routerProps}>{children}</MemoryRouter>
        </WorkflowExecutionProvider>
      </QueryClientProvider>
    </ThemeProvider>
  );
}

interface ExtendedRenderOptions extends Omit<RenderOptions, "wrapper"> {
  routerProps?: MemoryRouterProps;
}

export function renderWithProviders(
  ui: ReactElement,
  { routerProps, ...options }: ExtendedRenderOptions = {},
) {
  return render(ui, {
    wrapper: ({ children }) => (
      <Providers routerProps={routerProps}>{children}</Providers>
    ),
    ...options,
  });
}

function matchesQuery(query: string, width: number): boolean {
  const max = /\(\s*max-width\s*:\s*(\d+)px\s*\)/.exec(query);
  if (max) return width <= Number(max[1]);
  const min = /\(\s*min-width\s*:\s*(\d+)px\s*\)/.exec(query);
  if (min) return width >= Number(min[1]);
  return false;
}

/**
 * Per-test viewport mock for components that branch on `useIsMobile()` or
 * other `matchMedia` consumers. The default stub in `setup.ts` returns
 * `matches: false` for every query — call this in a test or `beforeEach`
 * to simulate a specific viewport width.
 *
 * Parses `(max-width: Npx)` and `(min-width: Npx)`; other media features
 * fall back to `false`.
 */
export function mockMatchMedia(width: number) {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    configurable: true,
    value: (query: string) => ({
      matches: matchesQuery(query, width),
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  });
}

export { screen, waitFor } from "@testing-library/react";
export { default as userEvent } from "@testing-library/user-event";
