import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useWorkflowPreview } from "./useWorkflowPreview";

// ─── Mock SSE transport ─────────────────────────────────────────

vi.mock("#/api/sse-client", () => ({
  connectToSSE: vi.fn(),
}));

import { connectToSSE } from "#/api/sse-client";
import { mockSSEWithEvents } from "#/test/sse-test-utils";

// ─── Mock editor store ──────────────────────────────────────────

vi.mock("#/stores/editor-store", () => ({
  useEditorStore: vi.fn((selector: (s: Record<string, unknown>) => unknown) =>
    selector({
      workflowId: 42,
      toWorkflowDef: () => ({ nodes: [], edges: [] }),
    }),
  ),
}));

// ─── Mock generated mutations ───────────────────────────────────

const mutateSaved = vi.fn();
const mutateUnsaved = vi.fn();
const savedIsPending = false;
const unsavedIsPending = false;

vi.mock("#/api/generated/workflows/workflows", () => ({
  usePreviewSavedWorkflowApiV1WorkflowsWorkflowIdPreviewPost: () => ({
    mutate: mutateSaved,
    isPending: savedIsPending,
  }),
  usePreviewUnsavedWorkflowApiV1WorkflowsPreviewPost: () => ({
    mutate: mutateUnsaved,
    isPending: unsavedIsPending,
  }),
}));

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

describe("useWorkflowPreview", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    mutateSaved.mockReset();
    mutateUnsaved.mockReset();
  });

  it("starts with idle state", () => {
    const { result } = renderHook(() => useWorkflowPreview(), {
      wrapper: createWrapper(),
    });

    expect(result.current.isPreviewRunning).toBe(false);
    expect(result.current.previewResult).toBeNull();
    expect(result.current.nodeStatuses.size).toBe(0);
    expect(result.current.error).toBeNull();
  });

  it("startPreview calls saved mutation when workflowId is set", () => {
    const { result } = renderHook(() => useWorkflowPreview(), {
      wrapper: createWrapper(),
    });

    act(() => {
      result.current.startPreview();
    });

    expect(mutateSaved).toHaveBeenCalledWith(
      { workflowId: 42 },
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    );
  });

  it("sets operationId from mutation response and connects SSE", async () => {
    mockSSEWithEvents([]);

    const { result } = renderHook(() => useWorkflowPreview(), {
      wrapper: createWrapper(),
    });

    act(() => {
      result.current.startPreview();
    });

    // Simulate mutation onSuccess callback
    const onSuccess = mutateSaved.mock.calls[0][1].onSuccess;
    act(() => {
      onSuccess({ status: 202, data: { operation_id: "preview-op-1" } });
    });

    await waitFor(() => {
      expect(connectToSSE).toHaveBeenCalledWith(
        "/api/v1/operations/preview-op-1/progress",
        expect.any(AbortSignal),
      );
    });
  });

  it("processes node_status events into nodeStatuses map", async () => {
    mockSSEWithEvents([
      {
        event: "node_status",
        data: JSON.stringify({
          node_id: "src_1",
          node_type: "source.liked_tracks",
          status: "completed",
          execution_order: 1,
          total_nodes: 2,
          output_track_count: 50,
        }),
      },
    ]);

    const { result } = renderHook(() => useWorkflowPreview(), {
      wrapper: createWrapper(),
    });

    act(() => {
      result.current.startPreview();
    });

    const onSuccess = mutateSaved.mock.calls[0][1].onSuccess;
    act(() => {
      onSuccess({ status: 202, data: { operation_id: "preview-op-2" } });
    });

    await waitFor(() => {
      const status = result.current.nodeStatuses.get("src_1");
      expect(status).toBeDefined();
      expect(status?.status).toBe("completed");
      expect(status?.outputTrackCount).toBe(50);
    });
  });

  it("sets previewResult on preview_complete event", async () => {
    mockSSEWithEvents([
      {
        event: "preview_complete",
        data: JSON.stringify({
          output_tracks: [
            { rank: 1, title: "Song A", artists: "Artist 1", isrc: "US1234" },
          ],
          node_summaries: [
            {
              node_id: "src_1",
              node_type: "source.liked_tracks",
              track_count: 1,
              sample_titles: ["Song A"],
            },
          ],
        }),
      },
    ]);

    const { result } = renderHook(() => useWorkflowPreview(), {
      wrapper: createWrapper(),
    });

    act(() => {
      result.current.startPreview();
    });

    const onSuccess = mutateSaved.mock.calls[0][1].onSuccess;
    act(() => {
      onSuccess({ status: 202, data: { operation_id: "preview-op-3" } });
    });

    await waitFor(() => {
      expect(result.current.previewResult).not.toBeNull();
      expect(result.current.previewResult?.output_tracks).toHaveLength(1);
      expect(result.current.previewResult?.output_tracks[0].title).toBe(
        "Song A",
      );
      expect(result.current.isPreviewRunning).toBe(false);
    });
  });

  it("sets error on SSE error event", async () => {
    mockSSEWithEvents([
      {
        event: "error",
        data: JSON.stringify({ error_message: "Source node failed" }),
      },
    ]);

    const { result } = renderHook(() => useWorkflowPreview(), {
      wrapper: createWrapper(),
    });

    act(() => {
      result.current.startPreview();
    });

    const onSuccess = mutateSaved.mock.calls[0][1].onSuccess;
    act(() => {
      onSuccess({ status: 202, data: { operation_id: "preview-op-4" } });
    });

    await waitFor(() => {
      expect(result.current.error?.message).toBe("Source node failed");
      expect(result.current.isPreviewRunning).toBe(false);
    });
  });

  it("sets error on mutation failure", () => {
    const { result } = renderHook(() => useWorkflowPreview(), {
      wrapper: createWrapper(),
    });

    act(() => {
      result.current.startPreview();
    });

    const onError = mutateSaved.mock.calls[0][1].onError;
    act(() => {
      onError(new Error("Network timeout"));
    });

    expect(result.current.error?.message).toBe("Network timeout");
    expect(result.current.isPreviewRunning).toBe(false);
  });

  it("clearPreview resets all state", async () => {
    mockSSEWithEvents([
      {
        event: "preview_complete",
        data: JSON.stringify({
          output_tracks: [{ rank: 1, title: "X", artists: "Y", isrc: null }],
          node_summaries: [],
        }),
      },
    ]);

    const { result } = renderHook(() => useWorkflowPreview(), {
      wrapper: createWrapper(),
    });

    // Start and complete a preview
    act(() => {
      result.current.startPreview();
    });

    const onSuccess = mutateSaved.mock.calls[0][1].onSuccess;
    act(() => {
      onSuccess({ status: 202, data: { operation_id: "preview-op-5" } });
    });

    await waitFor(() => {
      expect(result.current.previewResult).not.toBeNull();
    });

    // Now clear
    act(() => {
      result.current.clearPreview();
    });

    expect(result.current.previewResult).toBeNull();
    expect(result.current.nodeStatuses.size).toBe(0);
    expect(result.current.error).toBeNull();
    expect(result.current.isPreviewRunning).toBe(false);
  });
});
