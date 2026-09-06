import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { createElement, type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { OperationRunSummarySchema } from "#/api/generated/model";
import { server } from "#/test/setup";

import {
  isTerminalProgress,
  type OperationProgress,
  type OperationStatus,
  useOperationProgress,
} from "./useOperationProgress";

// ─── Mock SSE transport ─────────────────────────────────────────

vi.mock("#/api/sse-client", () => ({
  connectToSSE: vi.fn(),
}));

import { connectToSSE } from "#/api/sse-client";
import {
  createTestQueryClient,
  seedQuery,
  wasInvalidated,
} from "#/test/query-utils";
import {
  mockSSEOpenStream,
  mockSSEWithEvents,
  sseFrame,
} from "#/test/sse-test-utils";

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

// ─── Durable-run helpers ────────────────────────────────────────

function makeRunRow(
  overrides: Partial<OperationRunSummarySchema> = {},
): OperationRunSummarySchema {
  return {
    id: "run-1",
    operation_id: "op-123",
    operation_type: "import_lastfm_history",
    started_at: "2026-01-01T10:00:00Z",
    ended_at: "2026-01-01T10:05:00Z",
    status: "complete",
    counts: { track_plays: 42 },
    issue_count: 0,
    retryable: false,
    initiated_by: "user",
    touched: [],
    ...overrides,
  };
}

/** Serve the audit-log list the recovery poll reads. */
function mockRunRows(...rows: OperationRunSummarySchema[]) {
  server.use(
    http.get("*/api/v1/operation-runs", () =>
      HttpResponse.json({ data: rows, limit: 20, next_cursor: null }),
    ),
  );
}

/** Recovery timings that keep the tests deterministic and fast. */
const FAST_RECOVERY = { resumeDelayMs: 0, recoveryPollIntervalMs: 20 } as const;

// ─── Tests ──────────────────────────────────────────────────────

describe("useOperationProgress", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    // Vitest 4's restoreAllMocks leaves vi.fn() history/implementations intact.
    vi.resetAllMocks();
  });

  it("returns null progress when operationId is null", () => {
    const { result } = renderHook(() => useOperationProgress(null), {
      wrapper: createWrapper(),
    });

    expect(result.current.progress).toBeNull();
    expect(result.current.isActive).toBe(false);
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

  it("handles started event", async () => {
    const { close } = mockSSEOpenStream([
      sseFrame("started", { total: 200, description: "Importing tracks" }),
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
    close();
  });

  it("handles progress event with metrics", async () => {
    const { close } = mockSSEOpenStream([
      sseFrame("progress", {
        current: 50,
        total: 100,
        message: "Halfway there",
        completion_percentage: 50.0,
        items_per_second: 2.5,
        eta_seconds: 20,
      }),
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
    close();
  });

  it("handles complete event", async () => {
    mockSSEWithEvents([sseFrame("complete", { final_status: "completed" })]);

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

  it("exposes per-operation counts from the terminal complete event", async () => {
    mockSSEWithEvents([
      sseFrame("complete", {
        final_status: "completed",
        counts: { track_plays: 42, errors: 0 },
      }),
    ]);

    const { result } = renderHook(() => useOperationProgress("op-123"), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.progress?.counts).toEqual({
        track_plays: 42,
        errors: 0,
      });
    });
  });

  it("handles error event from server", async () => {
    mockSSEWithEvents([
      sseFrame("error", { error_message: "Import failed: rate limited" }),
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
    // The empty frame must contribute nothing and must not break the stream:
    // the event after it still lands.
    const { close } = mockSSEOpenStream([
      { event: "progress", data: "" },
      sseFrame("progress", { current: 7, message: "After empty event" }),
    ]);

    const { result } = renderHook(() => useOperationProgress("op-123"), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.progress).toEqual(
        expect.objectContaining({
          status: "running",
          current: 7,
          message: "After empty event",
        }),
      );
    });
    close();
  });

  it("resets state when operationId changes to null", async () => {
    const { close } = mockSSEOpenStream();

    const { result, rerender } = renderHook(
      ({ id }: { id: string | null }) => useOperationProgress(id),
      {
        wrapper: createWrapper(),
        initialProps: { id: "op-123" as string | null },
      },
    );

    expect(result.current.progress?.status).toBe("pending");

    rerender({ id: null });

    await waitFor(() => {
      expect(result.current.progress).toBeNull();
      expect(result.current.isActive).toBe(false);
    });
    close();
  });

  it("keeps a run with no audit row yet unresolved, not failed", async () => {
    mockSSEError("SSE connection failed: 404");
    mockRunRows();

    const { result } = renderHook(
      () => useOperationProgress("op-bad", FAST_RECOVERY),
      { wrapper: createWrapper() },
    );

    // A dead socket is not a dead run — the card reads as reconnecting while
    // the durable row is still being polled for.
    await waitFor(() => {
      expect(result.current.progress?.status).toBe("reconnecting");
    });
    expect(result.current.isActive).toBe(true);
  });

  it("invalidates what the server says the run touched, on complete", async () => {
    mockSSEWithEvents([
      sseFrame("complete", {
        final_status: "completed",
        touched: ["checkpoints"],
      }),
    ]);

    const queryClient = createTestQueryClient();
    seedQuery(queryClient, ["/api/v1/imports/checkpoints"]);

    renderHook(() => useOperationProgress("op-123"), {
      wrapper: createWrapper(queryClient),
    });

    await waitFor(() => {
      expect(wasInvalidated(queryClient, ["/api/v1/imports/checkpoints"])).toBe(
        true,
      );
    });
  });

  it("skips malformed JSON and processes subsequent valid events", async () => {
    const { close } = mockSSEOpenStream([
      { event: "progress", data: "not valid json{{{" },
      sseFrame("progress", {
        current: 10,
        total: 50,
        message: "After bad event",
      }),
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
    close();
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
    const { close } = mockSSEOpenStream([
      sseFrame("started", { description: "Second operation" }),
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
    close();
  });

  it("suppresses AbortError and stays in the pending state", async () => {
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
  });

  it("invalidates on error too — a failed run may have written", async () => {
    mockSSEWithEvents([
      sseFrame("error", {
        error_message: "Rate limit exceeded",
        touched: ["checkpoints"],
      }),
    ]);

    const queryClient = createTestQueryClient();
    seedQuery(queryClient, ["/api/v1/imports/checkpoints"]);

    renderHook(() => useOperationProgress("op-123"), {
      wrapper: createWrapper(queryClient),
    });

    await waitFor(() => {
      expect(wasInvalidated(queryClient, ["/api/v1/imports/checkpoints"])).toBe(
        true,
      );
    });
  });

  it("handles sub_operation_started event", async () => {
    mockSSEWithEvents([
      sseFrame("started", { total: 100, description: "Running workflow" }),
      sseFrame("sub_operation_started", {
        operation_id: "sub-1",
        description: "Fetching Last.fm metadata",
        total: 50,
        phase: "enrich",
      }),
    ]);

    const { result } = renderHook(() => useOperationProgress("op-123"), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.progress?.subOperation).toEqual(
        expect.objectContaining({
          operationId: "sub-1",
          description: "Fetching Last.fm metadata",
          total: 50,
          phase: "enrich",
        }),
      );
    });
  });

  it("handles sub_progress event", async () => {
    mockSSEWithEvents([
      sseFrame("started", { total: 100, description: "Running workflow" }),
      sseFrame("sub_operation_started", {
        operation_id: "sub-1",
        description: "Fetching metadata",
        total: 50,
      }),
      sseFrame("sub_progress", {
        operation_id: "sub-1",
        current: 25,
        total: 50,
        message: "Processed 25/50",
        completion_percentage: 50.0,
      }),
    ]);

    const { result } = renderHook(() => useOperationProgress("op-123"), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.progress?.subOperation).toEqual(
        expect.objectContaining({
          current: 25,
          total: 50,
          message: "Processed 25/50",
          completionPercentage: 50.0,
        }),
      );
    });
  });

  it("handles sub_operation_completed event", async () => {
    const { close } = mockSSEOpenStream([
      sseFrame("started", { total: 100, description: "Running workflow" }),
      sseFrame("sub_operation_started", {
        operation_id: "sub-1",
        description: "Fetching metadata",
        total: 50,
      }),
      sseFrame("sub_operation_completed", {
        operation_id: "sub-1",
        final_status: "completed",
      }),
    ]);

    const { result } = renderHook(() => useOperationProgress("op-123"), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      // After sub_operation_completed, subOperation should be null
      expect(result.current.progress?.subOperation).toBeNull();
      // But the main operation should still be running
      expect(result.current.progress?.status).toBe("running");
    });
    close();
  });

  // ─── Depth: an item's row owns everything happening under it ──
  //
  // A drain streams its files as sub-operations, and each file streams its own
  // phases underneath. The server tags every event with the generation the
  // reader renders as a row (`item_operation_id`), so the client never has to
  // reconstruct the tree — and a phase two levels down still lands on the file
  // it belongs to rather than opening a row of its own.

  it("attributes a nested phase's progress to the item that owns it", async () => {
    mockSSEWithEvents([
      sseFrame("started", { total: 2, description: "Import Spotify Export" }),
      sseFrame("sub_operation_started", {
        operation_id: "file-1",
        item_operation_id: "file-1",
        description: "Import Spotify History",
      }),
      sseFrame("sub_progress", {
        operation_id: "resolve-1",
        item_operation_id: "file-1",
        current: 812,
        total: 1204,
        message: "Resolved 812/1204 plays (spotify)",
        completion_percentage: 67.4,
        phase: "match",
      }),
    ]);

    const { result } = renderHook(() => useOperationProgress("drain-op"), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      // One row, keyed to the file — not two, and not keyed to the phase.
      expect(
        Object.keys(result.current.progress?.subOperationHistory ?? {}),
      ).toEqual(["file-1"]);
    });
    expect(result.current.progress?.subOperationHistory["file-1"]).toEqual(
      expect.objectContaining({ operationId: "file-1", phase: "match" }),
    );
  });

  it("keeps a settled item's counts once its own stream has closed", async () => {
    // These arrive exactly once, on the parent's stream. The item's own stream
    // closes moments later, so there is no second chance to read them.
    const { close } = mockSSEOpenStream([
      sseFrame("started", { total: 2, description: "Import Spotify Export" }),
      sseFrame("sub_operation_started", {
        operation_id: "file-1",
        item_operation_id: "file-1",
        description: "Import Spotify History",
      }),
      sseFrame("sub_operation_completed", {
        operation_id: "file-1",
        item_operation_id: "file-1",
        final_status: "completed",
        counts: { track_plays: 14208 },
      }),
    ]);

    const { result } = renderHook(() => useOperationProgress("drain-op"), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.progress?.subOperationHistory["file-1"]).toEqual(
        expect.objectContaining({
          outcome: "succeeded",
          counts: { track_plays: 14208 },
        }),
      );
    });
    close();
  });

  it("records a crashed item as failed, and a cancelled one not at all", async () => {
    // `completed` is not the only non-`failed` terminal an item can carry: a
    // crash is a failure, and a cancellation is neither column.
    const { close } = mockSSEOpenStream([
      sseFrame("started", { total: 2, description: "Import Spotify Export" }),
      sseFrame("sub_operation_started", {
        operation_id: "file-1",
        item_operation_id: "file-1",
        description: "Import Spotify History",
      }),
      sseFrame("sub_operation_completed", {
        operation_id: "file-1",
        item_operation_id: "file-1",
        final_status: "crashed",
      }),
      sseFrame("sub_operation_started", {
        operation_id: "file-2",
        item_operation_id: "file-2",
        description: "Import Spotify History",
      }),
      sseFrame("sub_operation_completed", {
        operation_id: "file-2",
        item_operation_id: "file-2",
        final_status: "cancelled",
      }),
    ]);

    const { result } = renderHook(() => useOperationProgress("drain-op"), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(
        result.current.progress?.subOperationHistory["file-1"]?.outcome,
      ).toBe("failed");
    });
    // A cancelled item is counted in no column, so its row keeps the null
    // outcome the start frame opened it with.
    expect(
      result.current.progress?.subOperationHistory["file-2"]?.outcome,
    ).toBeNull();
    // It still retires the live row it was occupying.
    expect(result.current.progress?.subOperation).toBeNull();
    close();
  });

  it("keeps what earlier frames recorded as a row is folded together", async () => {
    // No single frame carries the whole row: the name arrives with the start,
    // the resolve counts with the progress, and the counts with the terminal.
    const { close } = mockSSEOpenStream([
      sseFrame("started", { total: 1, description: "Import playlists" }),
      sseFrame("sub_operation_started", {
        operation_id: "sub-1",
        connector_playlist_identifier: "spotify:1",
        playlist_name: "Late Night",
        phase: "fetch",
        description: "Importing Late Night",
      }),
      sseFrame("sub_progress", {
        operation_id: "sub-1",
        connector_playlist_identifier: "spotify:1",
        current: 10,
        total: 10,
        message: "Resolved",
        resolved: 9,
        unresolved: 1,
        canonical_playlist_id: "pl-1",
      }),
    ]);

    const { result } = renderHook(() => useOperationProgress("op-123"), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.progress?.subOperationHistory["spotify:1"]).toEqual(
        {
          operationId: "sub-1",
          connectorPlaylistIdentifier: "spotify:1",
          // Carried over from the start frame, which the progress frame omits.
          playlistName: "Late Night",
          phase: "fetch",
          outcome: null,
          resolved: 9,
          unresolved: 1,
          errorMessage: null,
          canonicalPlaylistId: "pl-1",
          counts: null,
        },
      );
    });
    close();
  });

  it("a phase finishing does not clear the item still running above it", async () => {
    // Otherwise the live row blanks between ingest and resolve — the same
    // between-things gap, one level down.
    const { close } = mockSSEOpenStream([
      sseFrame("started", { total: 2, description: "Import Spotify Export" }),
      sseFrame("sub_operation_started", {
        operation_id: "ingest-1",
        item_operation_id: "file-1",
        description: "Ingesting",
        phase: "fetch",
      }),
      sseFrame("sub_operation_completed", {
        operation_id: "ingest-1",
        item_operation_id: "file-1",
        final_status: "completed",
      }),
    ]);

    const { result } = renderHook(() => useOperationProgress("drain-op"), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.progress?.status).toBe("running");
    });
    expect(result.current.progress?.subOperation).not.toBeNull();
    // And crucially the ROW is not concluded: a phase signs off with the same
    // `final_status: "completed"` a finished file does, so recording it as the
    // row's verdict would check off file 1 the moment its ingest ends — and
    // wipe the counts a later terminal would have carried.
    expect(
      result.current.progress?.subOperationHistory["file-1"]?.outcome ?? null,
    ).toBeNull();
    close();
  });

  // ─── Recovery: the durable row outlives the stream ────────────
  //
  // Prod incident: the machine running a long import was stopped mid-run. The
  // socket died, the client had no reconnect, and the card latched "Connection
  // failed" forever — while the operation_runs row held the real outcome.

  describe("recovery", () => {
    /** Stream dies mid-run, and the resume 404s: the queue is gone. */
    function mockStreamLostThenQueueGone() {
      vi.mocked(connectToSSE)
        .mockImplementationOnce(async () =>
          (async function* () {
            yield sseFrame("progress", { current: 10, total: 100 }, "evt_9");
            throw new Error("network error");
          })(),
        )
        .mockRejectedValueOnce(new Error("SSE connection failed: 404"));
    }

    it("resumes the stream with Last-Event-ID rather than giving up", async () => {
      vi.mocked(connectToSSE)
        .mockImplementationOnce(async () =>
          (async function* () {
            yield sseFrame("progress", { current: 10, total: 100 }, "evt_9");
            throw new Error("network error");
          })(),
        )
        .mockImplementationOnce(async () =>
          (async function* () {
            yield sseFrame(
              "complete",
              { counts: { track_plays: 42 } },
              "evt_10",
            );
          })(),
        );

      const { result } = renderHook(
        () => useOperationProgress("op-123", FAST_RECOVERY),
        { wrapper: createWrapper() },
      );

      await waitFor(() => {
        expect(result.current.progress?.status).toBe("completed");
      });
      expect(connectToSSE).toHaveBeenNthCalledWith(
        2,
        "/api/v1/operations/op-123/progress",
        expect.any(AbortSignal),
        { lastEventId: "evt_9" },
      );
      expect(result.current.progress?.message).not.toMatch(/Connection failed/);
    });

    it("shows an honest intermediate state while the durable row still says running", async () => {
      mockStreamLostThenQueueGone();
      mockRunRows(makeRunRow({ status: "running", ended_at: null }));

      const { result } = renderHook(
        () => useOperationProgress("op-123", FAST_RECOVERY),
        { wrapper: createWrapper() },
      );

      await waitFor(() => {
        expect(result.current.progress?.status).toBe("reconnecting");
      });
      expect(result.current.progress?.message).toBe(
        "Connection lost — checking result…",
      );
      // Still "in flight" as far as the UI is concerned — no re-trigger.
      expect(result.current.isActive).toBe(true);
    });

    it("renders the terminal state from a completed run row", async () => {
      mockStreamLostThenQueueGone();
      mockRunRows(
        makeRunRow({ status: "complete", counts: { track_plays: 42 } }),
      );

      const { result } = renderHook(
        () => useOperationProgress("op-123", FAST_RECOVERY),
        { wrapper: createWrapper() },
      );

      await waitFor(() => {
        expect(result.current.progress?.status).toBe("completed");
      });
      expect(result.current.progress?.message).toBe("Complete");
      expect(result.current.progress?.counts).toEqual({ track_plays: 42 });
      expect(result.current.isActive).toBe(false);
    });

    it("carries a partial run's issue count in counts.errors for the toast", async () => {
      // `issueCountFromCounts` reads counts.errors — the live `complete` event
      // rides the failure count there, so the recovered row must match it or a
      // lossy run gets announced as a clean success.
      mockStreamLostThenQueueGone();
      mockRunRows(
        makeRunRow({
          status: "partial",
          counts: { track_plays: 98 },
          issue_count: 2,
        }),
      );

      const { result } = renderHook(
        () => useOperationProgress("op-123", FAST_RECOVERY),
        { wrapper: createWrapper() },
      );

      await waitFor(() => {
        expect(result.current.progress?.status).toBe("completed");
      });
      expect(result.current.progress?.counts).toEqual({
        track_plays: 98,
        errors: 2,
      });
      expect(result.current.progress?.message).toBe("Completed with issues");
    });

    it("renders a failed run row as failed", async () => {
      mockStreamLostThenQueueGone();
      mockRunRows(makeRunRow({ status: "error", issue_count: 3 }));

      const { result } = renderHook(
        () => useOperationProgress("op-123", FAST_RECOVERY),
        { wrapper: createWrapper() },
      );

      await waitFor(() => {
        expect(result.current.progress?.status).toBe("failed");
      });
      expect(result.current.progress?.message).toBe("Operation failed");
      expect(result.current.progress?.counts).toEqual({
        track_plays: 42,
        errors: 3,
      });
    });

    it("invalidates the run row's tags when recovery resolves the run", async () => {
      // The dropped-stream path must invalidate identically to a live frame —
      // it is the case most likely to strand a stale screen.
      mockStreamLostThenQueueGone();
      mockRunRows(makeRunRow({ status: "complete", touched: ["checkpoints"] }));

      const queryClient = createTestQueryClient();
      seedQuery(queryClient, ["/api/v1/imports/checkpoints"]);

      renderHook(() => useOperationProgress("op-123", FAST_RECOVERY), {
        wrapper: createWrapper(queryClient),
      });

      await waitFor(() => {
        expect(
          wasInvalidated(queryClient, ["/api/v1/imports/checkpoints"]),
        ).toBe(true);
      });
    });

    it("recovers a stream that ended without a terminal frame", async () => {
      // A clean EOF never fires the stall watchdog — this used to latch the
      // card at "Connection lost" with no way back.
      mockSSEWithEvents([sseFrame("progress", { current: 10, total: 100 })]);
      mockRunRows(makeRunRow({ status: "complete" }));

      const { result } = renderHook(
        () => useOperationProgress("op-123", FAST_RECOVERY),
        { wrapper: createWrapper() },
      );

      await waitFor(() => {
        expect(result.current.progress?.status).toBe("completed");
      });
      expect(connectToSSE).toHaveBeenCalledTimes(1);
    });

    it("falls back to Connection failed only when the API is unreachable too", async () => {
      mockStreamLostThenQueueGone();
      server.use(
        http.get("*/api/v1/operation-runs", () =>
          HttpResponse.json(
            { error: { code: "INTERNAL", message: "down" } },
            { status: 500 },
          ),
        ),
      );

      const { result } = renderHook(
        () => useOperationProgress("op-123", FAST_RECOVERY),
        { wrapper: createWrapper() },
      );

      await waitFor(() => {
        expect(result.current.progress?.status).toBe("failed");
      });
      expect(result.current.progress?.message).toBe("Connection failed");
    });
  });

  it("clears a still-active sub-op on complete (lost sub_operation_completed)", async () => {
    mockSSEWithEvents([
      sseFrame("started", { total: 100, description: "Running workflow" }),
      sseFrame("sub_operation_started", {
        operation_id: "sub-1",
        description: "Fetching metadata",
        total: 50,
      }),
      // No sub_operation_completed — the terminal `complete` must still clear the
      // sub-op so the UI can't show a spinning bar under a "Complete" badge.
      sseFrame("complete", {}),
    ]);

    const { result } = renderHook(() => useOperationProgress("op-123"), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.progress?.status).toBe("completed");
      expect(result.current.progress?.subOperation).toBeNull();
    });
  });
});

describe("isTerminalProgress", () => {
  function progressWith(status: OperationStatus): OperationProgress {
    return {
      status,
      current: 0,
      total: null,
      message: "",
      description: null,
      completionPercentage: null,
      itemsPerSecond: null,
      etaSeconds: null,
      counts: null,
      subOperation: null,
      subOperationHistory: {},
    };
  }

  it.each<OperationStatus>(["completed", "failed", "cancelled"])(
    "treats %s as terminal",
    (status) => {
      expect(isTerminalProgress(progressWith(status))).toBe(true);
    },
  );

  it.each<OperationStatus>(["pending", "running", "reconnecting"])(
    "treats %s as still live",
    (status) => {
      expect(isTerminalProgress(progressWith(status))).toBe(false);
    },
  );

  it("treats a run that never started as not terminal", () => {
    expect(isTerminalProgress(null)).toBe(false);
  });
});
