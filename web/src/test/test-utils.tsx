import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { type RenderOptions, render } from "@testing-library/react";
import { type ReactElement, type ReactNode, useState } from "react";
import { MemoryRouter, type MemoryRouterProps } from "react-router";

import { ThemeProvider } from "#/contexts/ThemeContext";
import { WorkflowExecutionProvider } from "#/contexts/WorkflowExecutionContext";

interface ProvidersProps {
  children: ReactNode;
  routerProps?: MemoryRouterProps;
}

export function createTestQueryClient() {
  return new QueryClient({
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

// Re-export everything from testing-library so tests import from one place
export { act, screen, waitFor, within } from "@testing-library/react";
export { default as userEvent } from "@testing-library/user-event";
