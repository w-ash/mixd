import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useOperationSSE } from "./useOperationSSE";

// ─── Mock SSE transport ─────────────────────────────────────────

// connectToSSE is the transport boundary. Mocked (not MSW) so a test controls
// frames one by one: hold a stream open, end it without a terminal, or throw
// mid-stream to drive the resume path.
vi.mock("#/api/sse-client", () => ({
  connectToSSE: vi.fn(),
}));

import { connectToSSE } from "#/api/sse-client";
import { mockSSEWithEvents } from "#/test/sse-test-utils";

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
    // A fresh run streams from frame one — no REST seed, so the gate stays shut.
    expect(result.current.recovery.active).toBe(false);

    await waitFor(() => {
      expect(connectToSSE).toHaveBeenCalledWith(
        "/api/v1/operations/op-123/progress",
        expect.any(AbortSignal),
      );
    });
  });

  it("delivers each decoded event to onDomainEvent with a reportTerminal fn", async () => {
    mockSSEWithEvents([
      {
        event: "progress",
        data: JSON.stringify({ operation_id: "op-evt", current: 42 }),
      },
    ]);
    const onDomainEvent = vi.fn();

    const { result } = renderHook(() => useOperationSSE({ onDomainEvent }));

    act(() => {
      result.current.start("op-evt");
    });

    await waitFor(() => {
      expect(onDomainEvent).toHaveBeenCalledWith(
        { event: "progress", data: { operation_id: "op-evt", current: 42 } },
        expect.any(Function),
      );
    });
  });

  it("reportTerminal() is idempotent — true once, then false — and stops the run", async () => {
    const results: boolean[] = [];
    mockSSEWithEvents([{ event: "complete", data: "{}" }]);

    const { result } = renderHook(() =>
      useOperationSSE({
        onDomainEvent: (event, reportTerminal) => {
          if (event.event === "complete") {
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
    // A later arbitration from outside the stream (a racing recovery seed)
    // is a no-op too.
    act(() => {
      expect(result.current.reportTerminal()).toBe(false);
    });
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

  it("surfaces transport errors once the bounded resume is spent", async () => {
    mockSSEError("SSE connection failed: 404");

    const { result } = renderHook(() =>
      useOperationSSE({ onDomainEvent: noopDomainEvent, resumeDelayMs: 0 }),
    );

    act(() => {
      result.current.start("op-bad");
    });

    await waitFor(() => {
      expect(result.current.error?.message).toMatch(/SSE connection failed/);
    });
  });

  it("opens the recovery gate as `unresumable` when the stream can't be resumed", async () => {
    // The transport is out of retries — REST is now the only source of truth,
    // so the gate must open rather than the run being declared dead.
    mockSSEError("SSE connection failed: 404");

    const { result } = renderHook(() =>
      useOperationSSE({ onDomainEvent: noopDomainEvent, resumeDelayMs: 0 }),
    );

    act(() => {
      result.current.start("op-lost");
    });

    await waitFor(() => {
      expect(result.current.recovery.reason).toBe("unresumable");
    });
    expect(result.current.recovery.active).toBe(true);
    expect(result.current.isRunning).toBe(true);
  });

  it("opens the recovery gate when the stream ends without a terminal frame", async () => {
    mockSSEWithEvents([{ event: "progress", data: '{"current":1}' }]);

    const { result } = renderHook(() =>
      useOperationSSE({ onDomainEvent: noopDomainEvent }),
    );

    act(() => {
      result.current.start("op-cut");
    });

    await waitFor(() => {
      expect(result.current.recovery.reason).toBe("unresumable");
    });
  });

  it("leaves the recovery gate shut when a terminal frame closed the stream", async () => {
    mockSSEWithEvents([{ event: "complete", data: "{}" }]);

    const { result } = renderHook(() =>
      useOperationSSE({
        onDomainEvent: (event, reportTerminal) => {
          if (event.event === "complete") reportTerminal();
        },
      }),
    );

    act(() => {
      result.current.start("op-clean");
    });

    await waitFor(() => {
      expect(result.current.isRunning).toBe(false);
    });
    expect(result.current.recovery.active).toBe(false);
    expect(result.current.recovery.reason).toBeNull();
  });

  it("calls onStreamEnd when the stream ends normally", async () => {
    const onStreamEnd = vi.fn();
    mockSSEWithEvents([{ event: "progress", data: "{}" }]);

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
