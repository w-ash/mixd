/**
 * Verifies the REST snapshot fallback hook:
 *   - skips polling when disabled (SSE healthy)
 *   - polls when enabled (SSE stalled)
 *   - parses the {data, status, headers} envelope
 *
 * Doesn't drive the full snapshot -> reconciliation flow; that's covered
 * by useWorkflowSSE.test.ts where the SSE state and node-status merge
 * are observable end-to-end.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("#/api/client", () => ({
  customFetch: vi.fn(),
}));

import { customFetch } from "#/api/client";

import {
  type OperationSnapshot,
  useOperationSnapshot,
} from "./useOperationSnapshot";

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

const sampleSnapshot: OperationSnapshot = {
  operation_id: "op-1",
  id: "run-1",
  workflow_id: "wf-1",
  status: "running",
  nodes: [
    {
      node_id: "n1",
      node_type: "source.playlist",
      status: "running",
      execution_order: 1,
    },
  ],
};

describe("useOperationSnapshot", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("does not call the endpoint when disabled", async () => {
    const { result } = renderHook(
      () => useOperationSnapshot("op-1", { enabled: false }),
      { wrapper: createWrapper() },
    );

    // Allow microtasks to flush
    await new Promise((r) => setTimeout(r, 10));
    expect(customFetch).not.toHaveBeenCalled();
    expect(result.current.data).toBeUndefined();
  });

  it("fetches and unwraps the envelope when enabled", async () => {
    vi.mocked(customFetch).mockResolvedValue({
      data: sampleSnapshot,
      status: 200,
      headers: new Headers(),
    });

    const { result } = renderHook(
      () => useOperationSnapshot("op-1", { enabled: true }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => {
      expect(result.current.data).toEqual(sampleSnapshot);
    });
    expect(customFetch).toHaveBeenCalledWith(
      "/api/v1/operations/op-1/snapshot",
    );
  });

  it("surfaces errors instead of swallowing them", async () => {
    vi.mocked(customFetch).mockRejectedValue(new Error("404"));

    const { result } = renderHook(
      () => useOperationSnapshot("op-bad", { enabled: true }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });
    // retry: false means the failed call doesn't burn into multiple
    // attempts inside Tanstack's retry loop. The refetchInterval may
    // still fire later, but the immediate failure is single-shot.
    expect(customFetch).toHaveBeenCalled();
  });
});
