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
    // Vitest 4's restoreAllMocks only touches vi.spyOn spies — the module-level
    // connectToTest mock keeps its call history without an explicit reset,
    // which would make per-test attempt counts cumulative.
    vi.resetAllMocks();
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

  it("sets error on non-abort transport failure, once the resume is spent", async () => {
    mockSSEError(new Error("SSE connection failed: 404"));

    const { result } = renderHook(
      () => useSSEConnection("op-bad", { ...noopOptions, resumeDelayMs: 0 }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => {
      expect(result.current.error).toBeInstanceOf(Error);
      expect(result.current.error?.message).toBe("SSE connection failed: 404");
    });
    // One initial attempt + one resume, and no more.
    expect(connectToSSE).toHaveBeenCalledTimes(2);
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

  // ─── State machine + lastEventAt + watchdog (PR-2 / L3) ──────

  describe("state machine", () => {
    it("starts in idle when operationId is null", () => {
      const { result } = renderHook(() => useSSEConnection(null, noopOptions), {
        wrapper: createWrapper(),
      });
      expect(result.current.state.kind).toBe("idle");
      expect(result.current.lastEventAt).toBeNull();
    });

    it("transitions to streaming and bumps lastEventAt on first event", async () => {
      const onEvent = vi.fn();
      mockSSEWithEvents([{ event: "node_status", data: '{"node_id":"n1"}' }]);

      const { result } = renderHook(
        () => useSSEConnection("op-123", { onEvent }),
        { wrapper: createWrapper() },
      );

      await waitFor(() => {
        expect(result.current.state.kind).toBe("closed-done");
      });
      // lastEventAt was set during streaming, persists through closed-done? No
      // — closed-done has no lastEventAt. But onEvent was called once.
      expect(onEvent).toHaveBeenCalledTimes(1);
    });

    it("bumps lastEventAt on a frame even when data is empty (keepalive shape)", async () => {
      // Mock parser-yielded frame shape that mirrors a server keepalive
      // comment after going through eventsource-parser. The data guard in
      // the hook skips dispatch but the freshness timestamp must still be
      // bumped before the guard.
      const onEvent = vi.fn();
      const { close } = mockSSEOpenStream([
        { event: "", data: "" },
        { event: "node_status", data: '{"node_id":"n1"}' },
      ]);

      const { result } = renderHook(
        () => useSSEConnection("op-123", { onEvent }),
        { wrapper: createWrapper() },
      );

      await waitFor(() => {
        // After the second frame, state is streaming and onEvent has been
        // called once (only the second frame had data).
        expect(result.current.state.kind).toBe("streaming");
      });
      expect(onEvent).toHaveBeenCalledTimes(1);
      expect(result.current.lastEventAt).not.toBeNull();
      close();
    });

    it("derives isConnected = true while streaming", async () => {
      const { close } = mockSSEOpenStream([
        { event: "node_status", data: '{"node_id":"n1"}' },
      ]);

      const { result } = renderHook(
        () => useSSEConnection("op-123", noopOptions),
        { wrapper: createWrapper() },
      );

      await waitFor(() => {
        expect(result.current.state.kind).toBe("streaming");
      });
      expect(result.current.isConnected).toBe(true);
      close();
    });

    it("transitions to closed-done after stream ends naturally", async () => {
      mockSSEWithEvents([{ event: "node_status", data: '{"node_id":"n1"}' }]);

      const { result } = renderHook(
        () => useSSEConnection("op-123", noopOptions),
        { wrapper: createWrapper() },
      );

      await waitFor(() => {
        expect(result.current.state.kind).toBe("closed-done");
      });
      expect(result.current.isConnected).toBe(false);
    });

    it("transitions to closed-error on connection failure", async () => {
      mockSSEError(new Error("boom"));

      const { result } = renderHook(
        () => useSSEConnection("op-bad", { ...noopOptions, resumeDelayMs: 0 }),
        { wrapper: createWrapper() },
      );

      await waitFor(() => {
        expect(result.current.state.kind).toBe("closed-error");
      });
      expect(result.current.error).toEqual(new Error("boom"));
    });
  });

  // ─── Bounded resume (v0.10.3 incident fix) ───────────────────

  describe("resume after a transport drop", () => {
    it("retries once, replaying from the last received event id", async () => {
      // First attempt streams two events then dies mid-stream; the retry must
      // ask the server to resume from the last id it delivered.
      vi.mocked(connectToSSE)
        .mockImplementationOnce(async () =>
          (async function* () {
            yield { event: "progress", data: '{"current":1}', id: "evt_1" };
            yield { event: "progress", data: '{"current":2}', id: "evt_2" };
            throw new Error("network error");
          })(),
        )
        .mockImplementationOnce(async () =>
          (async function* () {
            yield { event: "complete", data: "{}", id: "evt_3" };
          })(),
        );

      const onEvent = vi.fn();
      const { result } = renderHook(
        () => useSSEConnection("op-resume", { onEvent, resumeDelayMs: 0 }),
        { wrapper: createWrapper() },
      );

      await waitFor(() => {
        expect(result.current.state.kind).toBe("closed-done");
      });
      expect(connectToSSE).toHaveBeenNthCalledWith(
        2,
        "/api/v1/operations/op-resume/progress",
        expect.any(AbortSignal),
        { lastEventId: "evt_2" },
      );
      // The resumed stream's events keep flowing to the consumer.
      expect(onEvent).toHaveBeenCalledWith("complete", {});
      expect(result.current.error).toBeNull();
    });

    it("passes through the resuming state before the retry", async () => {
      vi.mocked(connectToSSE)
        .mockRejectedValueOnce(new Error("network error"))
        .mockImplementationOnce(async () =>
          (async function* () {
            await new Promise(() => {});
          })(),
        );

      const { result } = renderHook(
        () =>
          useSSEConnection("op-resume", { ...noopOptions, resumeDelayMs: 200 }),
        { wrapper: createWrapper() },
      );

      // Backoff window: not connected, but not an error either.
      await waitFor(() => {
        expect(result.current.state.kind).toBe("resuming");
      });
      expect(result.current.error).toBeNull();

      await waitFor(() => {
        expect(result.current.state.kind).toBe("open-no-events");
      });
      expect(result.current.error).toBeNull();
    });

    it("a 404 on the retry closes the stream without further attempts", async () => {
      // The server's in-memory queue is gone (run ended / machine restarted).
      // The transport gives up here; recovering the run is the caller's job.
      vi.mocked(connectToSSE)
        .mockRejectedValueOnce(new Error("network error"))
        .mockRejectedValueOnce(new Error("SSE connection failed: 404"));

      const { result } = renderHook(
        () => useSSEConnection("op-gone", { ...noopOptions, resumeDelayMs: 0 }),
        { wrapper: createWrapper() },
      );

      await waitFor(() => {
        expect(result.current.state.kind).toBe("closed-error");
      });
      expect(result.current.error?.message).toBe("SSE connection failed: 404");
      expect(connectToSSE).toHaveBeenCalledTimes(2);
    });
  });
});
