import { QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { createTestQueryClient } from "@/test/test-utils";

import { useWorkflowSSE } from "./useWorkflowSSE";

// ─── Mock SSE transport ─────────────────────────────────────────

vi.mock("@/api/sse-client", () => ({
  connectToSSE: vi.fn(),
}));

import { connectToSSE, mockSSEWithEvents } from "@/test/sse-test-utils";

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
    expect(result.current.error).toBeNull();
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
});
