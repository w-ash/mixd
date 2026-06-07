import { QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { createTestQueryClient } from "#/test/test-utils";

import { useWorkflowSSE } from "./useWorkflowSSE";

// ─── Mock SSE transport ─────────────────────────────────────────

vi.mock("#/api/sse-client", () => ({
  connectToSSE: vi.fn(),
}));

// The snapshot fallback fetches through customFetch; mock it so adopt() can be
// driven without a live backend. Existing tests never enable the snapshot query
// (it only turns on while stalled or adopting), so this is inert for them.
vi.mock("#/api/client", () => ({
  customFetch: vi.fn(),
}));

import { customFetch } from "#/api/client";
import { connectToSSE } from "#/api/sse-client";
import { mockSSEWithEvents } from "#/test/sse-test-utils";

// ─── Test wrapper ───────────────────────────────────────────────

function createWrapper() {
  const client = createTestQueryClient();
  return function Wrapper({ children }: { children: ReactNode }) {
    return createElement(QueryClientProvider, { client }, children);
  };
}

// ─── Tests ──────────────────────────────────────────────────────

describe("useWorkflowSSE", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("starts with idle state", () => {
    const { result } = renderHook(() => useWorkflowSSE(), {
      wrapper: createWrapper(),
    });

    expect(result.current.operationId).toBeNull();
    expect(result.current.isRunning).toBe(false);
    expect(result.current.nodeStatuses.size).toBe(0);
    expect(result.current.runAccepted).toBe(false);
    expect(result.current.error).toBeNull();
  });

  it("flips runAccepted when run_accepted event arrives", async () => {
    mockSSEWithEvents([
      {
        event: "run_accepted",
        data: JSON.stringify({
          operation_id: "op-rxa",
          run_id: "run-rxa",
          workflow_id: "wf-rxa",
          task_count: 3,
          accepted_at: "2026-05-09T00:00:00Z",
        }),
      },
    ]);

    const { result } = renderHook(() => useWorkflowSSE(), {
      wrapper: createWrapper(),
    });

    act(() => {
      result.current.start("op-rxa");
    });

    await waitFor(() => {
      expect(result.current.runAccepted).toBe(true);
    });
    // run_accepted should not be treated as a completion or error
    expect(result.current.error).toBeNull();
  });

  it("start() resets runAccepted to false for the next run", async () => {
    mockSSEWithEvents([
      { event: "run_accepted", data: JSON.stringify({ operation_id: "a" }) },
    ]);

    const { result } = renderHook(() => useWorkflowSSE(), {
      wrapper: createWrapper(),
    });

    act(() => {
      result.current.start("op-a");
    });

    await waitFor(() => {
      expect(result.current.runAccepted).toBe(true);
    });

    mockSSEWithEvents([]);
    act(() => {
      result.current.start("op-b");
    });
    expect(result.current.runAccepted).toBe(false);
  });

  it("start() sets operationId and isRunning, connects SSE", async () => {
    mockSSEWithEvents([]);

    const { result } = renderHook(() => useWorkflowSSE(), {
      wrapper: createWrapper(),
    });

    act(() => {
      result.current.start("op-123");
    });

    expect(result.current.operationId).toBe("op-123");
    expect(result.current.isRunning).toBe(true);

    await waitFor(() => {
      expect(connectToSSE).toHaveBeenCalledWith(
        "/api/v1/operations/op-123/progress",
        expect.any(AbortSignal),
      );
    });
  });

  it("forwards node_status events to nodeStatuses map", async () => {
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
          duration_ms: 300,
          output_track_count: 42,
        }),
      },
    ]);

    const { result } = renderHook(() => useWorkflowSSE(), {
      wrapper: createWrapper(),
    });

    act(() => {
      result.current.start("op-456");
    });

    await waitFor(() => {
      const status = result.current.nodeStatuses.get("source_1");
      expect(status).toBeDefined();
      expect(status?.status).toBe("completed");
      expect(status?.durationMs).toBe(300);
      expect(status?.outputTrackCount).toBe(42);
    });
  });

  it("sets error on SSE error event and calls onError", async () => {
    const onError = vi.fn();

    mockSSEWithEvents([
      {
        event: "error",
        data: JSON.stringify({ error_message: "Node failed: timeout" }),
      },
    ]);

    const { result } = renderHook(() => useWorkflowSSE({ onError }), {
      wrapper: createWrapper(),
    });

    act(() => {
      result.current.start("op-err");
    });

    await waitFor(() => {
      expect(result.current.error?.message).toBe("Node failed: timeout");
      expect(result.current.isRunning).toBe(false);
      expect(onError).toHaveBeenCalledOnce();
    });
  });

  it("treats a crashed final_status on the error channel as an error", async () => {
    const onError = vi.fn();

    mockSSEWithEvents([
      {
        event: "error",
        data: JSON.stringify({
          final_status: "crashed",
          error_message: "worker died",
        }),
      },
    ]);

    const { result } = renderHook(() => useWorkflowSSE({ onError }), {
      wrapper: createWrapper(),
    });

    act(() => {
      result.current.start("op-crashed");
    });

    await waitFor(() => {
      expect(result.current.error?.message).toBe("worker died");
      expect(result.current.isRunning).toBe(false);
      expect(onError).toHaveBeenCalledOnce();
    });
  });

  it("treats a cancelled final_status as a graceful terminal, not an error", async () => {
    const onComplete = vi.fn();
    const onError = vi.fn();

    mockSSEWithEvents([
      {
        event: "error",
        data: JSON.stringify({
          final_status: "cancelled",
          error_message: "Cancelled by server",
        }),
      },
    ]);

    const { result } = renderHook(
      () => useWorkflowSSE({ onComplete, onError }),
      { wrapper: createWrapper() },
    );

    act(() => {
      result.current.start("op-cancelled");
    });

    await waitFor(() => {
      expect(result.current.isRunning).toBe(false);
      expect(onComplete).toHaveBeenCalledWith("cancelled", expect.anything());
    });
    // A graceful cancellation must not surface as an error.
    expect(result.current.error).toBeNull();
    expect(onError).not.toHaveBeenCalled();
  });

  it("uses custom errorFallbackMessage when error_message missing", async () => {
    mockSSEWithEvents([
      {
        event: "error",
        data: JSON.stringify({}),
      },
    ]);

    const { result } = renderHook(
      () => useWorkflowSSE({ errorFallbackMessage: "Preview failed" }),
      { wrapper: createWrapper() },
    );

    act(() => {
      result.current.start("op-fallback");
    });

    await waitFor(() => {
      expect(result.current.error?.message).toBe("Preview failed");
    });
  });

  it("calls onComplete for completion events and sets isRunning=false", async () => {
    const onComplete = vi.fn();

    mockSSEWithEvents([
      {
        event: "complete",
        data: JSON.stringify({ result: "done" }),
      },
    ]);

    const { result } = renderHook(() => useWorkflowSSE({ onComplete }), {
      wrapper: createWrapper(),
    });

    act(() => {
      result.current.start("op-complete");
    });

    await waitFor(() => {
      expect(result.current.isRunning).toBe(false);
      expect(onComplete).toHaveBeenCalledWith("complete", { result: "done" });
    });
  });

  it("recognizes custom completion events", async () => {
    const onComplete = vi.fn();

    mockSSEWithEvents([
      {
        event: "preview_complete",
        data: JSON.stringify({ output_tracks: [] }),
      },
    ]);

    const { result } = renderHook(
      () =>
        useWorkflowSSE({
          completionEvents: new Set(["complete", "preview_complete"]),
          onComplete,
        }),
      { wrapper: createWrapper() },
    );

    act(() => {
      result.current.start("op-preview");
    });

    await waitFor(() => {
      expect(result.current.isRunning).toBe(false);
      expect(onComplete).toHaveBeenCalledWith("preview_complete", {
        output_tracks: [],
      });
    });
  });

  it("ignores events not in completionEvents set", async () => {
    const onComplete = vi.fn();

    mockSSEWithEvents([
      {
        event: "some_unknown_event",
        data: JSON.stringify({ info: "extra" }),
      },
      {
        event: "complete",
        data: JSON.stringify({}),
      },
    ]);

    const { result } = renderHook(() => useWorkflowSSE({ onComplete }), {
      wrapper: createWrapper(),
    });

    act(() => {
      result.current.start("op-ignore");
    });

    await waitFor(() => {
      // onComplete only called once — for "complete", not "some_unknown_event"
      expect(onComplete).toHaveBeenCalledOnce();
      expect(onComplete).toHaveBeenCalledWith("complete", {});
    });
  });

  it("start() resets previous error and nodeStatuses", async () => {
    // First: trigger an error
    mockSSEWithEvents([
      {
        event: "error",
        data: JSON.stringify({ error_message: "first error" }),
      },
    ]);

    const { result } = renderHook(() => useWorkflowSSE(), {
      wrapper: createWrapper(),
    });

    act(() => {
      result.current.start("op-first");
    });

    await waitFor(() => {
      expect(result.current.error).not.toBeNull();
    });

    // Second: start again — error should clear
    mockSSEWithEvents([]);

    act(() => {
      result.current.start("op-second");
    });

    expect(result.current.error).toBeNull();
    expect(result.current.nodeStatuses.size).toBe(0);
    expect(result.current.isRunning).toBe(true);
  });

  it("reset() clears all state and disconnects", async () => {
    mockSSEWithEvents([
      {
        event: "node_status",
        data: JSON.stringify({
          node_id: "n1",
          node_type: "filter",
          status: "running",
          execution_order: 1,
          total_nodes: 1,
        }),
      },
    ]);

    const { result } = renderHook(() => useWorkflowSSE(), {
      wrapper: createWrapper(),
    });

    act(() => {
      result.current.start("op-reset");
    });

    await waitFor(() => {
      expect(result.current.nodeStatuses.size).toBe(1);
    });

    act(() => {
      result.current.reset();
    });

    expect(result.current.operationId).toBeNull();
    expect(result.current.isRunning).toBe(false);
    expect(result.current.nodeStatuses.size).toBe(0);
    expect(result.current.error).toBeNull();
  });

  it("adopt() seeds node state from the snapshot immediately (no SSE stall)", async () => {
    // SSE connects but emits nothing — adoption must still paint current state.
    mockSSEWithEvents([]);
    vi.mocked(customFetch).mockResolvedValue({
      data: {
        operation_id: "op-adopt",
        id: "run-adopt",
        workflow_id: "wf-adopt",
        status: "running",
        nodes: [
          {
            node_id: "n1",
            node_type: "source.playlist",
            status: "completed",
            execution_order: 1,
          },
          {
            node_id: "n2",
            node_type: "filter.by_metric",
            status: "running",
            execution_order: 2,
          },
        ],
      },
      status: 200,
      headers: new Headers(),
    });

    const { result } = renderHook(() => useWorkflowSSE(), {
      wrapper: createWrapper(),
    });

    act(() => {
      result.current.adopt("op-adopt");
    });

    // Seeded from the snapshot without waiting for a 45s stall.
    await waitFor(() => {
      expect(result.current.nodeStatuses.size).toBe(2);
    });
    expect(result.current.isRunning).toBe(true);
    expect(customFetch).toHaveBeenCalledWith(
      "/api/v1/operations/op-adopt/snapshot",
    );
  });
});
