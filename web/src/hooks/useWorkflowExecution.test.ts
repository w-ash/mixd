import { type QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { createElement, type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { WorkflowExecutionProvider } from "#/contexts/WorkflowExecutionContext";
import { seedQuery, wasInvalidated } from "#/test/query-utils";
import { server } from "#/test/setup";
import {
  mockSSEOpenStream,
  mockSSEWithEvents,
  sseFrame,
} from "#/test/sse-test-utils";
import { createTestQueryClient } from "#/test/test-utils";
import { useWorkflowExecution } from "./useWorkflowExecution";

// ─── Mock SSE transport ─────────────────────────────────────────

// connectToSSE is the transport boundary. Mocked (not MSW) so a test controls
// frames one by one: hold a stream open, end it without a terminal, or throw
// mid-stream to drive the resume path.
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

  it("execute starts the run and tracks the handles the server returned", async () => {
    let requestedWorkflowId: string | undefined;
    server.use(
      http.post("*/api/v1/workflows/:workflowId/run", ({ params }) => {
        requestedWorkflowId = String(params.workflowId);
        return HttpResponse.json(
          { operation_id: "op-run-7", run_id: "run-7" },
          { status: 202 },
        );
      }),
    );
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
    });
    expect(requestedWorkflowId).toBe("019d0000-0000-7000-8000-000000000001");
    expect(result.current.operationId).toBe("op-run-7");
    expect(result.current.runId).toBe("run-7");
  });

  it("processes node_status SSE events into nodeStatuses map", async () => {
    mockSSEWithEvents([
      sseFrame("node_status", {
        node_id: "source_1",
        node_type: "source.liked_tracks",
        status: "running",
        execution_order: 1,
        total_nodes: 2,
      }),
      sseFrame("node_status", {
        node_id: "source_1",
        node_type: "source.liked_tracks",
        status: "completed",
        execution_order: 1,
        total_nodes: 2,
        duration_ms: 450,
        input_track_count: 0,
        output_track_count: 50,
      }),
      sseFrame("complete", {}),
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

  describe("cache reconciliation", () => {
    const WORKFLOW_ID = "019d0000-0000-7000-8000-000000000001";

    /**
     * Seed each URL as a cached query so invalidation is observable as state
     * rather than as call arguments — the matching is a predicate now.
     */
    function seed(client: QueryClient, urls: string[]) {
      for (const url of urls) seedQuery(client, [url]);
    }

    it("invalidates the app-global run sources on run START", async () => {
      // Without this the list page waits out the 25s idle poll before showing
      // the row as running.
      mockSSEOpenStream([]);

      const queryClient = createTestQueryClient();
      seed(queryClient, ["/api/v1/workflows/active-runs", "/api/v1/workflows"]);

      const { result } = renderHook(() => useWorkflowExecution(WORKFLOW_ID), {
        wrapper: createWrapper(queryClient),
      });

      act(() => {
        result.current.execute();
      });

      await waitFor(() => {
        expect(
          wasInvalidated(queryClient, ["/api/v1/workflows/active-runs"]),
        ).toBe(true);
      });
      expect(wasInvalidated(queryClient, ["/api/v1/workflows"])).toBe(true);
    });

    it("invalidates the run detail query on terminal", async () => {
      // The run-detail page was previously never reconciled, so an open run
      // page stayed on its "running" snapshot forever.
      mockSSEWithEvents([sseFrame("complete", {})]);

      const queryClient = createTestQueryClient();
      const urls = [
        `/api/v1/workflows/${WORKFLOW_ID}/runs/run-1`,
        `/api/v1/workflows/${WORKFLOW_ID}`,
        `/api/v1/workflows/${WORKFLOW_ID}/runs`,
        "/api/v1/workflows",
        "/api/v1/workflows/active-runs",
      ];
      seed(queryClient, urls);

      const { result } = renderHook(() => useWorkflowExecution(WORKFLOW_ID), {
        wrapper: createWrapper(queryClient),
      });

      act(() => {
        result.current.execute();
      });

      await waitFor(() => {
        expect(result.current.isExecuting).toBe(false);
      });

      for (const url of urls) {
        expect(wasInvalidated(queryClient, [url]), url).toBe(true);
      }
    });
  });

  it("sets error on SSE error event", async () => {
    mockSSEWithEvents([
      sseFrame("error", { error_message: "Node failed: API timeout" }),
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
