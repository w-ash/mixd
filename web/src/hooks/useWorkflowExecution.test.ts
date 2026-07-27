import { type QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { WorkflowExecutionProvider } from "#/contexts/WorkflowExecutionContext";
import { mockSSEOpenStream, mockSSEWithEvents } from "#/test/sse-test-utils";
import { createTestQueryClient } from "#/test/test-utils";

import { useWorkflowExecution } from "./useWorkflowExecution";

// ─── Mock SSE transport ─────────────────────────────────────────

vi.mock("#/api/sse-client", () => ({
  connectToSSE: vi.fn(),
}));

// ─── Test wrapper ───────────────────────────────────────────────

function createWrapper(queryClient?: QueryClient) {
  const client = queryClient ?? createTestQueryClient();
  return function Wrapper({ children }: { children: ReactNode }) {
    return createElement(
      QueryClientProvider,
      { client },
      createElement(WorkflowExecutionProvider, null, children),
    );
  };
}

// ─── Tests ──────────────────────────────────────────────────────

describe("useWorkflowExecution", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("starts with idle state", () => {
    const { result } = renderHook(
      () => useWorkflowExecution("019d0000-0000-7000-8000-000000000001"),
      {
        wrapper: createWrapper(),
      },
    );

    expect(result.current.isExecuting).toBe(false);
    expect(result.current.operationId).toBeNull();
    expect(result.current.runId).toBeNull();
    expect(result.current.nodeStatuses.size).toBe(0);
    expect(result.current.error).toBeNull();
  });

  it("execute triggers mutation and sets executing state", async () => {
    // The default MSW handler returns 202 with { operation_id, run_id }.
    // Keep the stream OPEN: an in-flight run's SSE doesn't close until terminal,
    // and a clean close without a terminal now triggers a snapshot reconcile.
    mockSSEOpenStream([]);

    const { result } = renderHook(
      () => useWorkflowExecution("019d0000-0000-7000-8000-000000000001"),
      {
        wrapper: createWrapper(),
      },
    );

    act(() => {
      result.current.execute();
    });

    await waitFor(() => {
      expect(result.current.isExecuting).toBe(true);
      expect(result.current.operationId).toBeTruthy();
      expect(result.current.runId).toEqual(expect.any(String));
    });
  });

  it("processes node_status SSE events into nodeStatuses map", async () => {
    mockSSEWithEvents([
      {
        event: "node_status",
        data: JSON.stringify({
          node_id: "source_1",
          node_type: "source.liked_tracks",
          status: "running",
          execution_order: 1,
          total_nodes: 2,
        }),
      },
      {
        event: "node_status",
        data: JSON.stringify({
          node_id: "source_1",
          node_type: "source.liked_tracks",
          status: "completed",
          execution_order: 1,
          total_nodes: 2,
          duration_ms: 450,
          input_track_count: 0,
          output_track_count: 50,
        }),
      },
      {
        event: "complete",
        data: JSON.stringify({}),
      },
    ]);

    const { result } = renderHook(
      () => useWorkflowExecution("019d0000-0000-7000-8000-000000000001"),
      {
        wrapper: createWrapper(),
      },
    );

    act(() => {
      result.current.execute();
    });

    await waitFor(() => {
      const nodeStatus = result.current.nodeStatuses.get("source_1");
      expect(nodeStatus).toBeDefined();
      expect(nodeStatus?.status).toBe("completed");
      expect(nodeStatus?.durationMs).toBe(450);
      expect(nodeStatus?.outputTrackCount).toBe(50);
    });
  });

  it("resets state on new execution", async () => {
    // First execution sets error
    mockSSEWithEvents([
      {
        event: "error",
        data: JSON.stringify({ error_message: "Something broke" }),
      },
    ]);

    const { result } = renderHook(
      () => useWorkflowExecution("019d0000-0000-7000-8000-000000000001"),
      {
        wrapper: createWrapper(),
      },
    );

    act(() => {
      result.current.execute();
    });

    await waitFor(() => {
      expect(result.current.error).not.toBeNull();
    });

    // Second execution should reset error and nodeStatuses. Keep the stream open
    // so the still-running run doesn't trigger a stream-end snapshot reconcile.
    mockSSEOpenStream([]);

    act(() => {
      result.current.execute();
    });

    // error is cleared synchronously in execute()
    await waitFor(() => {
      expect(result.current.error).toBeNull();
      expect(result.current.nodeStatuses.size).toBe(0);
    });
  });

  describe("cache reconciliation", () => {
    const WORKFLOW_ID = "019d0000-0000-7000-8000-000000000001";

    /** Every queryKey passed to invalidateQueries, flattened to its URL head. */
    function invalidatedUrls(spy: { mock: { calls: unknown[][] } }): string[] {
      return spy.mock.calls.flatMap((call) => {
        const key = (call[0] as { queryKey?: unknown[] } | undefined)?.queryKey;
        return typeof key?.[0] === "string" ? [key[0]] : [];
      });
    }

    it("invalidates the app-global run sources on run START", async () => {
      // Without this the list page waits out the 25s idle poll before showing
      // the row as running.
      mockSSEOpenStream([]);

      const queryClient = createTestQueryClient();
      const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");

      const { result } = renderHook(() => useWorkflowExecution(WORKFLOW_ID), {
        wrapper: createWrapper(queryClient),
      });

      act(() => {
        result.current.execute();
      });

      await waitFor(() => {
        expect(invalidatedUrls(invalidateSpy)).toContain(
          "/api/v1/workflows/active-runs",
        );
      });
      expect(invalidatedUrls(invalidateSpy)).toContain("/api/v1/workflows");
    });

    it("invalidates the run detail query on terminal", async () => {
      // The run-detail page was previously never reconciled, so an open run
      // page stayed on its "running" snapshot forever.
      mockSSEWithEvents([{ event: "complete", data: JSON.stringify({}) }]);

      const queryClient = createTestQueryClient();
      const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");

      const { result } = renderHook(() => useWorkflowExecution(WORKFLOW_ID), {
        wrapper: createWrapper(queryClient),
      });

      act(() => {
        result.current.execute();
      });

      await waitFor(() => {
        expect(result.current.isExecuting).toBe(false);
      });

      const urls = invalidatedUrls(invalidateSpy);
      expect(
        urls.some((u) =>
          new RegExp(`^/api/v1/workflows/${WORKFLOW_ID}/runs/.+`).test(u),
        ),
      ).toBe(true);
      expect(urls).toContain(`/api/v1/workflows/${WORKFLOW_ID}`);
      expect(urls).toContain(`/api/v1/workflows/${WORKFLOW_ID}/runs`);
      expect(urls).toContain("/api/v1/workflows");
      expect(urls).toContain("/api/v1/workflows/active-runs");
    });
  });

  it("sets error on SSE error event", async () => {
    mockSSEWithEvents([
      {
        event: "error",
        data: JSON.stringify({ error_message: "Node failed: API timeout" }),
      },
    ]);

    const { result } = renderHook(
      () => useWorkflowExecution("019d0000-0000-7000-8000-000000000001"),
      {
        wrapper: createWrapper(),
      },
    );

    act(() => {
      result.current.execute();
    });

    await waitFor(() => {
      expect(result.current.error?.message).toBe("Node failed: API timeout");
      expect(result.current.isExecuting).toBe(false);
    });
  });
});
