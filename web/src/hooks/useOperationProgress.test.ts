import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { SSEEvent } from "@/api/sse-client";

import { useOperationProgress } from "./useOperationProgress";

// ─── Mock SSE transport ─────────────────────────────────────────

vi.mock("@/api/sse-client", () => ({
  connectToSSE: vi.fn(),
}));

import { connectToSSE } from "@/api/sse-client";

/** Mock connectToSSE to resolve with a finite sequence of events. */
function mockSSEWithEvents(events: SSEEvent[]) {
  const gen = async function* () {
    for (const e of events) yield e;
  };
  vi.mocked(connectToSSE).mockResolvedValue(gen());
}

/** Mock connectToSSE to reject with an error. */
function mockSSEError(message: string) {
  vi.mocked(connectToSSE).mockRejectedValue(new Error(message));
}

// ─── Test wrapper ───────────────────────────────────────────────

function createWrapper(queryClient?: QueryClient) {
  const client =
    queryClient ??
    new QueryClient({
      defaultOptions: {
        queries: { retry: false, gcTime: 0 },
        mutations: { retry: false },
      },
    });
  return function Wrapper({ children }: { children: ReactNode }) {
    return createElement(QueryClientProvider, { client }, children);
  };
}

// ─── Tests ──────────────────────────────────────────────────────

describe("useOperationProgress", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("returns null progress when operationId is null", () => {
    const { result } = renderHook(() => useOperationProgress(null), {
      wrapper: createWrapper(),
    });

    expect(result.current.progress).toBeNull();
    expect(result.current.isConnected).toBe(false);
    expect(result.current.error).toBeNull();
  });

  it("sets initial pending state before connection opens", () => {
    // Mock that stays pending until abort — mirrors real connectToSSE's
    // use of fetch(url, { signal }) which rejects on abort.
    vi.mocked(connectToSSE).mockImplementation((_url, signal) => {
      return new Promise((_resolve, reject) => {
        signal.addEventListener(
          "abort",
          () => reject(new DOMException("Aborted", "AbortError")),
          { once: true },
        );
      });
    });

    const { result } = renderHook(() => useOperationProgress("op-123"), {
      wrapper: createWrapper(),
    });

    expect(result.current.progress).toEqual(
      expect.objectContaining({
        status: "pending",
        message: "Connecting...",
      }),
    );
  });

  it("connects and sets isConnected on open", async () => {
    mockSSEWithEvents([]);

    const { result } = renderHook(() => useOperationProgress("op-123"), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isConnected).toBe(true);
    });
  });

  it("handles started event", async () => {
    mockSSEWithEvents([
      {
        event: "started",
        data: JSON.stringify({ total: 200, description: "Importing tracks" }),
      },
    ]);

    const { result } = renderHook(() => useOperationProgress("op-123"), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.progress).toEqual(
        expect.objectContaining({
          status: "running",
          total: 200,
          message: "Importing tracks",
        }),
      );
    });
  });

  it("handles progress event with metrics", async () => {
    mockSSEWithEvents([
      {
        event: "progress",
        data: JSON.stringify({
          current: 50,
          total: 100,
          message: "Halfway there",
          completion_percentage: 50.0,
          items_per_second: 2.5,
          eta_seconds: 20,
        }),
      },
    ]);

    const { result } = renderHook(() => useOperationProgress("op-123"), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.progress).toEqual(
        expect.objectContaining({
          status: "running",
          current: 50,
          total: 100,
          message: "Halfway there",
          completionPercentage: 50.0,
          itemsPerSecond: 2.5,
          etaSeconds: 20,
        }),
      );
    });
  });

  it("handles complete event", async () => {
    mockSSEWithEvents([
      {
        event: "complete",
        data: JSON.stringify({ final_status: "completed" }),
      },
    ]);

    const { result } = renderHook(() => useOperationProgress("op-123"), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.progress).toEqual(
        expect.objectContaining({
          status: "completed",
          message: "Complete",
        }),
      );
    });
  });

  it("handles error event from server", async () => {
    mockSSEWithEvents([
      {
        event: "error",
        data: JSON.stringify({ message: "Import failed: rate limited" }),
      },
    ]);

    const { result } = renderHook(() => useOperationProgress("op-123"), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.progress).toEqual(
        expect.objectContaining({
          status: "failed",
          message: "Import failed: rate limited",
        }),
      );
    });
  });

  it("ignores events with empty data", async () => {
    mockSSEWithEvents([{ event: "progress", data: "" }]);

    const { result } = renderHook(() => useOperationProgress("op-123"), {
      wrapper: createWrapper(),
    });

    // Wait for connection + stream consumption, then verify status unchanged
    await waitFor(() => {
      expect(result.current.isConnected).toBe(true);
    });
    expect(result.current.progress?.status).toBe("pending");
  });

  it("resets state when operationId changes to null", async () => {
    mockSSEWithEvents([]);

    const { result, rerender } = renderHook(
      ({ id }: { id: string | null }) => useOperationProgress(id),
      {
        wrapper: createWrapper(),
        initialProps: { id: "op-123" as string | null },
      },
    );

    await waitFor(() => {
      expect(result.current.isConnected).toBe(true);
    });

    rerender({ id: null });

    await waitFor(() => {
      expect(result.current.progress).toBeNull();
      expect(result.current.isConnected).toBe(false);
    });
  });

  it("sets error on connection failure", async () => {
    mockSSEError("SSE connection failed: 404");

    const { result } = renderHook(() => useOperationProgress("op-bad"), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.error).toBeInstanceOf(Error);
      expect(result.current.error?.message).toMatch(/SSE connection failed/);
    });
  });

  it("invalidates custom query keys on complete", async () => {
    mockSSEWithEvents([
      {
        event: "complete",
        data: JSON.stringify({ final_status: "completed" }),
      },
    ]);

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 } },
    });
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");

    renderHook(
      () =>
        useOperationProgress("op-123", {
          invalidateKeys: [["/api/v1/imports/checkpoints"]],
        }),
      { wrapper: createWrapper(queryClient) },
    );

    await waitFor(() => {
      expect(invalidateSpy).toHaveBeenCalledWith({
        queryKey: ["/api/v1/imports/checkpoints"],
      });
    });
  });

  it("skips malformed JSON and processes subsequent valid events", async () => {
    mockSSEWithEvents([
      { event: "progress", data: "not valid json{{{" },
      {
        event: "progress",
        data: JSON.stringify({
          current: 10,
          total: 50,
          message: "After bad event",
        }),
      },
    ]);

    const { result } = renderHook(() => useOperationProgress("op-123"), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.progress).toEqual(
        expect.objectContaining({
          status: "running",
          current: 10,
          message: "After bad event",
        }),
      );
    });
    // No error surfaced — malformed events are silently skipped
    expect(result.current.error).toBeNull();
  });

  it("aborts first connection when operationId changes to a different value", async () => {
    // First connection: stays open until abort
    let firstAborted = false;
    vi.mocked(connectToSSE).mockImplementation((_url, signal) => {
      return new Promise((resolve, reject) => {
        signal.addEventListener(
          "abort",
          () => {
            firstAborted = true;
            reject(new DOMException("Aborted", "AbortError"));
          },
          { once: true },
        );
        // Resolve with empty iterable after a tick (simulates open connection)
        setTimeout(
          () =>
            resolve(
              (async function* () {
                // Yield nothing — just hold the connection open
                await new Promise(() => {});
              })(),
            ),
          0,
        );
      });
    });

    const { result, rerender } = renderHook(
      ({ id }: { id: string | null }) => useOperationProgress(id),
      {
        wrapper: createWrapper(),
        initialProps: { id: "op-first" as string | null },
      },
    );

    // Wait for first connection to establish
    await waitFor(() => {
      expect(result.current.progress?.status).toBe("pending");
    });

    // Now switch to second operationId — should abort the first
    mockSSEWithEvents([
      {
        event: "started",
        data: JSON.stringify({ description: "Second operation" }),
      },
    ]);
    rerender({ id: "op-second" });

    await waitFor(() => {
      expect(firstAborted).toBe(true);
    });

    await waitFor(() => {
      expect(result.current.progress).toEqual(
        expect.objectContaining({
          status: "running",
          message: "Second operation",
        }),
      );
    });
  });

  it("suppresses AbortError without setting error state", async () => {
    vi.mocked(connectToSSE).mockRejectedValue(
      new DOMException("The operation was aborted", "AbortError"),
    );

    const { result } = renderHook(() => useOperationProgress("op-123"), {
      wrapper: createWrapper(),
    });

    // Give time for the async IIFE to run and handle the AbortError
    await waitFor(() => {
      // The pending state is set synchronously, so it should exist
      expect(result.current.progress?.status).toBe("pending");
    });

    // AbortError should NOT surface — no error state set
    expect(result.current.error).toBeNull();
  });

  it("invalidates query keys on error event", async () => {
    mockSSEWithEvents([
      {
        event: "error",
        data: JSON.stringify({ message: "Rate limit exceeded" }),
      },
    ]);

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 } },
    });
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");

    renderHook(
      () =>
        useOperationProgress("op-123", {
          invalidateKeys: [["/api/v1/imports/checkpoints"]],
        }),
      { wrapper: createWrapper(queryClient) },
    );

    await waitFor(() => {
      expect(invalidateSpy).toHaveBeenCalledWith({
        queryKey: ["/api/v1/imports/checkpoints"],
      });
    });
  });
});
