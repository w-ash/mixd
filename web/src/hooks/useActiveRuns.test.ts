/**
 * Verifies the app-global active-runs source: useActiveRun selects the single
 * run for one workflow, or null.
 *
 * The adaptive polling cadence and execution-context invalidation are behaviour
 * of the surrounding wiring; here we pin the data-shaping the detail page
 * depends on.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("#/api/client", () => ({
  customFetch: vi.fn(),
}));

import { customFetch } from "#/api/client";
import { useActiveRun } from "./useActiveRuns";

function createWrapper() {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  return function Wrapper({ children }: { children: ReactNode }) {
    return createElement(QueryClientProvider, { client }, children);
  };
}

function run(workflowId: string, operationId: string) {
  return {
    id: `run-${workflowId}`,
    workflow_id: workflowId,
    status: "running" as const,
    operation_id: operationId,
  };
}

function mockActiveRuns(runs: ReturnType<typeof run>[]) {
  vi.mocked(customFetch).mockResolvedValue({
    data: { data: runs, total: runs.length, limit: 50, offset: 0 },
    status: 200,
    headers: new Headers(),
  });
}

describe("useActiveRun", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("selects the run for the matching workflow", async () => {
    mockActiveRuns([run("wf-1", "op-1"), run("wf-2", "op-2")]);

    const { result } = renderHook(() => useActiveRun("wf-2"), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.data).not.toBeUndefined());
    expect(result.current.data?.operation_id).toBe("op-2");
  });

  it("returns null when no run matches the workflow", async () => {
    mockActiveRuns([run("wf-1", "op-1")]);

    const { result } = renderHook(() => useActiveRun("wf-other"), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toBeNull();
  });
});
