import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useSSEConnection } from "./useSSEConnection";

// ─── Mock SSE transport ─────────────────────────────────────────

// connectToSSE is the transport boundary. Mocked (not MSW) so a test controls
// frames one by one: hold a stream open, end it without a terminal, or throw
// mid-stream to drive the resume path.
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

    expect(result.current.state.kind).toBe("idle");
    expect(result.current.lastEventAt).toBeNull();
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

  it("calls onEvent with the decoded event for each SSE frame", async () => {
    const onEvent = vi.fn();
    mockSSEWithEvents([
      {
        event: "node_status",
        data: JSON.stringify({ node_id: "src_1", status: "running" }),
      },
      {
        event: "complete",
        data: JSON.stringify({ final_status: "completed" }),
      },
    ]);

    renderHook(() => useSSEConnection("op-123", { onEvent }), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(onEvent).toHaveBeenCalledTimes(2);
    });

    expect(onEvent).toHaveBeenCalledWith({
      event: "node_status",
      data: { node_id: "src_1", status: "running" },
    });
    expect(onEvent).toHaveBeenCalledWith({
      event: "complete",
      data: { final_status: "completed" },
    });
  });

  it("skips a frame whose event name is not in the SSE vocabulary", async () => {
    const onEvent = vi.fn();
    mockSSEWithEvents([
      { event: "message", data: JSON.stringify({ hello: true }) },
      { event: "progress", data: JSON.stringify({ current: 3 }) },
    ]);

    renderHook(() => useSSEConnection("op-123", { onEvent }), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(onEvent).toHaveBeenCalledTimes(1);
    });
    expect(onEvent).toHaveBeenCalledWith({
      event: "progress",
      data: { current: 3 },
    });
  });

  it("treats an AbortError as our own teardown: no resume, no error", async () => {
    mockSSEError(new DOMException("Aborted", "AbortError"));

    const { result } = renderHook(
      // Zero backoff, so a wrongly-attempted resume would land inside the wait.
      () => useSSEConnection("op-123", { ...noopOptions, resumeDelayMs: 0 }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => {
      expect(connectToSSE).toHaveBeenCalledTimes(1);
    });
    await act(() => new Promise((r) => setTimeout(r, 20)));

    expect(connectToSSE).toHaveBeenCalledTimes(1);
    expect(result.current.state.kind).toBe("connecting");
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

  it("disconnect aborts the open stream and closes cleanly", async () => {
    const { close } = mockSSEOpenStream([
      { event: "progress", data: '{"current":1}' },
    ]);

    const { result } = renderHook(
      () => useSSEConnection("op-123", noopOptions),
      { wrapper: createWrapper() },
    );

    await waitFor(() => {
      expect(result.current.state.kind).toBe("streaming");
    });
    const signal = vi.mocked(connectToSSE).mock.calls[0][1];

    act(() => {
      result.current.disconnect();
    });

    expect(signal.aborted).toBe(true);
    expect(result.current.state.kind).toBe("closed-done");
    expect(result.current.isConnected).toBe(false);
    expect(result.current.error).toBeNull();
    close();
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
    it("bumps lastEventAt on a frame even when data is empty (keepalive shape)", async () => {
      // A server keepalive comment reaches the hook as an empty frame. It is
      // never dispatched, but it must still count as liveness, or a quiet run
      // kept alive by keepalives alone would trip the stall watchdog.
      const now = 1_700_000_000_000;
      vi.spyOn(Date, "now").mockReturnValue(now);
      const onEvent = vi.fn();
      const { close } = mockSSEOpenStream([{ event: "", data: "" }]);

      const { result } = renderHook(
        () => useSSEConnection("op-123", { onEvent }),
        { wrapper: createWrapper() },
      );

      await waitFor(() => {
        expect(result.current.state.kind).toBe("streaming");
      });
      expect(result.current.lastEventAt).toBe(now);
      expect(onEvent).not.toHaveBeenCalled();
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
      expect(onEvent).toHaveBeenCalledWith({ event: "complete", data: {} });
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
