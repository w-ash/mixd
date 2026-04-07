import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useSSEConnection } from "./useSSEConnection";

// ─── Mock SSE transport ─────────────────────────────────────────

vi.mock("#/api/sse-client", () => ({
  connectToSSE: vi.fn(),
}));

import { connectToSSE } from "#/api/sse-client";
import { mockSSEOpenStream, mockSSEWithEvents } from "#/test/sse-test-utils";

function mockSSEError(error: Error) {
  vi.mocked(connectToSSE).mockRejectedValue(error);
}

// ─── Wrapper ────────────────────────────────────────────────────

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

// ─── Tests ──────────────────────────────────────────────────────

describe("useSSEConnection", () => {
  const noopOptions = { onEvent: vi.fn() };

  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("returns idle state when operationId is null", () => {
    const { result } = renderHook(() => useSSEConnection(null, noopOptions), {
      wrapper: createWrapper(),
    });

    expect(result.current.isConnected).toBe(false);
    expect(result.current.error).toBeNull();
    expect(connectToSSE).not.toHaveBeenCalled();
  });

  it("connects and sets isConnected when operationId is provided", async () => {
    const { close } = mockSSEOpenStream();

    const { result } = renderHook(
      () => useSSEConnection("op-123", noopOptions),
      { wrapper: createWrapper() },
    );

    await waitFor(() => {
      expect(result.current.isConnected).toBe(true);
    });
    expect(connectToSSE).toHaveBeenCalledWith(
      "/api/v1/operations/op-123/progress",
      expect.any(AbortSignal),
    );
    close();
  });

  it("calls onEvent with parsed JSON for each SSE event", async () => {
    const onEvent = vi.fn();
    mockSSEWithEvents([
      {
        event: "node_status",
        data: JSON.stringify({ node_id: "src_1", status: "running" }),
      },
      {
        event: "complete",
        data: JSON.stringify({ done: true }),
      },
    ]);

    renderHook(() => useSSEConnection("op-123", { onEvent }), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(onEvent).toHaveBeenCalledTimes(2);
    });

    expect(onEvent).toHaveBeenCalledWith("node_status", {
      node_id: "src_1",
      status: "running",
    });
    expect(onEvent).toHaveBeenCalledWith("complete", { done: true });
  });

  it("skips events with empty data", async () => {
    const onEvent = vi.fn();
    mockSSEWithEvents([
      { event: "progress", data: "" },
      {
        event: "progress",
        data: JSON.stringify({ current: 5 }),
      },
    ]);

    renderHook(() => useSSEConnection("op-123", { onEvent }), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(onEvent).toHaveBeenCalledTimes(1);
    });
    expect(onEvent).toHaveBeenCalledWith("progress", { current: 5 });
  });

  it("skips malformed JSON without breaking the stream", async () => {
    const onEvent = vi.fn();
    mockSSEWithEvents([
      { event: "progress", data: "not-json{{{" },
      {
        event: "progress",
        data: JSON.stringify({ current: 10 }),
      },
    ]);

    renderHook(() => useSSEConnection("op-123", { onEvent }), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(onEvent).toHaveBeenCalledTimes(1);
    });
    expect(onEvent).toHaveBeenCalledWith("progress", { current: 10 });
  });

  it("suppresses AbortError without setting error state", async () => {
    mockSSEError(new DOMException("Aborted", "AbortError"));

    const { result } = renderHook(
      () => useSSEConnection("op-123", noopOptions),
      { wrapper: createWrapper() },
    );

    // Give the async IIFE time to run
    await waitFor(() => {
      expect(connectToSSE).toHaveBeenCalled();
    });

    expect(result.current.error).toBeNull();
  });

  it("sets error on non-abort transport failure", async () => {
    mockSSEError(new Error("SSE connection failed: 404"));

    const { result } = renderHook(
      () => useSSEConnection("op-bad", noopOptions),
      { wrapper: createWrapper() },
    );

    await waitFor(() => {
      expect(result.current.error).toBeInstanceOf(Error);
      expect(result.current.error?.message).toBe("SSE connection failed: 404");
    });
  });

  it("calls onStreamEnd when the event iterator completes", async () => {
    const onStreamEnd = vi.fn();
    mockSSEWithEvents([{ event: "complete", data: JSON.stringify({}) }]);

    renderHook(
      () => useSSEConnection("op-123", { onEvent: vi.fn(), onStreamEnd }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => {
      expect(onStreamEnd).toHaveBeenCalledTimes(1);
    });
  });

  it("disconnect aborts the connection", async () => {
    // Connection that stays open until aborted
    vi.mocked(connectToSSE).mockImplementation((_url, signal) => {
      return new Promise((_resolve, reject) => {
        signal.addEventListener(
          "abort",
          () => reject(new DOMException("Aborted", "AbortError")),
          { once: true },
        );
      });
    });

    const { result } = renderHook(
      () => useSSEConnection("op-123", noopOptions),
      { wrapper: createWrapper() },
    );

    // Verify the connection was initiated
    expect(connectToSSE).toHaveBeenCalled();

    // Disconnect should abort without error
    result.current.disconnect();

    await waitFor(() => {
      expect(result.current.isConnected).toBe(false);
    });
    expect(result.current.error).toBeNull();
  });

  it("aborts previous connection when operationId changes", async () => {
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
        setTimeout(
          () =>
            resolve(
              (async function* () {
                await new Promise(() => {});
              })(),
            ),
          0,
        );
      });
    });

    const { rerender } = renderHook(
      ({ id }: { id: string | null }) => useSSEConnection(id, noopOptions),
      {
        wrapper: createWrapper(),
        initialProps: { id: "op-first" as string | null },
      },
    );

    // Switch to second operationId
    mockSSEWithEvents([]);
    rerender({ id: "op-second" });

    await waitFor(() => {
      expect(firstAborted).toBe(true);
    });
  });
});
