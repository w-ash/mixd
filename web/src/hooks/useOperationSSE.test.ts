import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useOperationSSE } from "./useOperationSSE";

// ─── Mock SSE transport ─────────────────────────────────────────

vi.mock("#/api/sse-client", () => ({
  connectToSSE: vi.fn(),
}));

import { connectToSSE } from "#/api/sse-client";
import { mockSSEOpenStream, mockSSEWithEvents } from "#/test/sse-test-utils";

/** Mock connectToSSE to reject with an error. */
function mockSSEError(message: string) {
  vi.mocked(connectToSSE).mockRejectedValue(new Error(message));
}

/** Inert handler for tests that don't care about event payloads. */
const noopDomainEvent = () => {};

describe("useOperationSSE", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("starts idle", () => {
    const { result } = renderHook(() =>
      useOperationSSE({ onDomainEvent: noopDomainEvent }),
    );

    expect(result.current.operationId).toBeNull();
    expect(result.current.isRunning).toBe(false);
    expect(result.current.isConnected).toBe(false);
    expect(result.current.error).toBeNull();
    expect(result.current.recovery.active).toBe(false);
  });

  it("start() sets operationId + isRunning and connects the progress SSE", async () => {
    mockSSEWithEvents([]);

    const { result } = renderHook(() =>
      useOperationSSE({ onDomainEvent: noopDomainEvent }),
    );

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

  it("delivers each parsed event to onDomainEvent with a reportTerminal fn", async () => {
    mockSSEWithEvents([
      { event: "custom", data: JSON.stringify({ value: 42 }) },
    ]);
    const onDomainEvent = vi.fn();

    const { result } = renderHook(() => useOperationSSE({ onDomainEvent }));

    act(() => {
      result.current.start("op-evt");
    });

    await waitFor(() => {
      expect(onDomainEvent).toHaveBeenCalledWith(
        "custom",
        { value: 42 },
        expect.any(Function),
      );
    });
  });

  it("reportTerminal() is idempotent — true once, then false — and stops the run", async () => {
    const results: boolean[] = [];
    mockSSEWithEvents([{ event: "go", data: "{}" }]);

    const { result } = renderHook(() =>
      useOperationSSE({
        onDomainEvent: (eventType, _d, reportTerminal) => {
          if (eventType === "go") {
            // Two arbitration attempts in one frame: first wins, rest no-op.
            results.push(reportTerminal(), reportTerminal());
          }
        },
      }),
    );

    act(() => {
      result.current.start("op-term");
    });

    await waitFor(() => {
      expect(results).toEqual([true, false]);
    });
    expect(result.current.isRunning).toBe(false);
  });

  it("a later terminal source cannot double-fire after the first", async () => {
    // First the SSE channel fires terminal, then the same hook reports again
    // (simulating a racing recovery seed) — the second must be a no-op.
    const calls: boolean[] = [];
    const { close } = mockSSEOpenStream([{ event: "done", data: "{}" }]);

    const { result } = renderHook(() =>
      useOperationSSE({
        onDomainEvent: (eventType, _d, reportTerminal) => {
          if (eventType === "done") calls.push(reportTerminal());
        },
      }),
    );

    act(() => {
      result.current.start("op-race");
    });

    await waitFor(() => {
      expect(calls).toEqual([true]);
    });
    // A second arbitration from any source returns false.
    act(() => {
      expect(result.current.reportTerminal()).toBe(false);
    });
    close();
  });

  it("calls onReset on start and on reset, and reset() clears state", async () => {
    const onReset = vi.fn();
    mockSSEWithEvents([]);

    const { result } = renderHook(() =>
      useOperationSSE({ onDomainEvent: noopDomainEvent, onReset }),
    );

    act(() => {
      result.current.start("op-reset");
    });
    expect(onReset).toHaveBeenCalledTimes(1);

    act(() => {
      result.current.reset();
    });

    expect(onReset).toHaveBeenCalledTimes(2);
    expect(result.current.operationId).toBeNull();
    expect(result.current.isRunning).toBe(false);
  });

  it("adopt() opens the recovery gate immediately; markSeeded() closes it", async () => {
    mockSSEWithEvents([]);

    const { result } = renderHook(() =>
      useOperationSSE({ onDomainEvent: noopDomainEvent }),
    );

    act(() => {
      result.current.adopt("op-adopt");
    });

    // Seed gate open right after adopt (no 45 s stall wait).
    expect(result.current.recovery.active).toBe(true);
    expect(result.current.isRunning).toBe(true);

    act(() => {
      result.current.recovery.markSeeded();
    });

    expect(result.current.recovery.active).toBe(false);
  });

  it("start() does NOT open the recovery gate (fresh run, no seed)", async () => {
    mockSSEWithEvents([]);

    const { result } = renderHook(() =>
      useOperationSSE({ onDomainEvent: noopDomainEvent }),
    );

    act(() => {
      result.current.start("op-fresh");
    });

    expect(result.current.recovery.active).toBe(false);
  });

  it("surfaces transport errors", async () => {
    mockSSEError("SSE connection failed: 404");

    const { result } = renderHook(() =>
      useOperationSSE({ onDomainEvent: noopDomainEvent }),
    );

    act(() => {
      result.current.start("op-bad");
    });

    await waitFor(() => {
      expect(result.current.error?.message).toMatch(/SSE connection failed/);
    });
  });

  it("calls onStreamEnd when the stream ends normally", async () => {
    const onStreamEnd = vi.fn();
    mockSSEWithEvents([{ event: "ping", data: "{}" }]);

    const { result } = renderHook(() =>
      useOperationSSE({ onDomainEvent: noopDomainEvent, onStreamEnd }),
    );

    act(() => {
      result.current.start("op-end");
    });

    await waitFor(() => {
      expect(onStreamEnd).toHaveBeenCalledOnce();
    });
  });
});
