import { QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { createElement, type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useEditorStore } from "#/stores/editor-store";
import { createTestQueryClient } from "#/test/query-utils";
import { server } from "#/test/setup";

import { useWorkflowPreview } from "./useWorkflowPreview";

// ─── Mock SSE transport ─────────────────────────────────────────

// connectToSSE is the transport boundary. Mocked (not MSW) so a test controls
// frames one by one: hold a stream open, end it without a terminal, or throw
// mid-stream to drive the resume path.
vi.mock("#/api/sse-client", () => ({
  connectToSSE: vi.fn(),
}));

import { connectToSSE } from "#/api/sse-client";
import { mockSSEWithEvents, sseFrame } from "#/test/sse-test-utils";

// ─── Preview endpoints ──────────────────────────────────────────

interface PreviewRequests {
  /** Workflow ids previewed from their saved server definition. */
  saved: string[];
  /** Definitions sent for an unsaved (canvas) preview. */
  unsaved: unknown[];
}

/** Both preview endpoints start `operationId`; record what each was sent. */
function mockPreviewStart(operationId: string): PreviewRequests {
  const requests: PreviewRequests = { saved: [], unsaved: [] };
  server.use(
    http.post("*/api/v1/workflows/preview", async ({ request }) => {
      requests.unsaved.push(await request.json());
      return HttpResponse.json({ operation_id: operationId }, { status: 202 });
    }),
    http.post("*/api/v1/workflows/:workflowId/preview", ({ params }) => {
      requests.saved.push(String(params.workflowId));
      return HttpResponse.json({ operation_id: operationId }, { status: 202 });
    }),
  );
  return requests;
}

function createWrapper() {
  const client = createTestQueryClient();
  return function Wrapper({ children }: { children: ReactNode }) {
    return createElement(QueryClientProvider, { client }, children);
  };
}

/** Start a preview of the saved, unchanged workflow `wf-42`. */
function renderStartedPreview() {
  const hook = renderHook(() => useWorkflowPreview(), {
    wrapper: createWrapper(),
  });
  act(() => {
    hook.result.current.startPreview();
  });
  return hook;
}

// ─── Tests ──────────────────────────────────────────────────────

describe("useWorkflowPreview", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.clearAllMocks();
    useEditorStore.setState({ workflowId: "wf-42", isDirty: false });
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

  it("previews the saved definition when the canvas is clean, and streams its operation", async () => {
    const requests = mockPreviewStart("preview-op-1");
    mockSSEWithEvents([]);

    renderStartedPreview();

    await waitFor(() => {
      expect(connectToSSE).toHaveBeenCalledWith(
        "/api/v1/operations/preview-op-1/progress",
        expect.any(AbortSignal),
      );
    });
    expect(requests).toEqual({ saved: ["wf-42"], unsaved: [] });
  });

  it("previews the canvas (not the stale server def) when saved but dirty", async () => {
    const requests = mockPreviewStart("preview-op-dirty");
    mockSSEWithEvents([]);
    // Pending canvas edits: the name differs from what the server holds.
    useEditorStore.setState({
      isDirty: true,
      workflowName: "Edited on canvas",
    });

    renderStartedPreview();

    await waitFor(() => {
      expect(requests.unsaved).toHaveLength(1);
    });
    expect(requests.saved).toEqual([]);
    expect(requests.unsaved[0]).toMatchObject({
      definition: { id: "wf-42", name: "Edited on canvas", tasks: [] },
    });
    await waitFor(() => {
      expect(connectToSSE).toHaveBeenCalledWith(
        "/api/v1/operations/preview-op-dirty/progress",
        expect.any(AbortSignal),
      );
    });
  });

  it("processes node_status events into nodeStatuses map", async () => {
    mockPreviewStart("preview-op-2");
    mockSSEWithEvents([
      sseFrame("node_status", {
        node_id: "src_1",
        node_type: "source.liked_tracks",
        status: "completed",
        execution_order: 1,
        total_nodes: 2,
        output_track_count: 50,
      }),
    ]);

    const { result } = renderStartedPreview();

    await waitFor(() => {
      expect(result.current.nodeStatuses.get("src_1")).toMatchObject({
        status: "completed",
        outputTrackCount: 50,
      });
    });
  });

  it("sets previewResult on preview_complete event", async () => {
    mockPreviewStart("preview-op-3");
    mockSSEWithEvents([
      sseFrame("preview_complete", {
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
        metric_columns: ["play_count"],
      }),
    ]);

    const { result } = renderStartedPreview();

    await waitFor(() => {
      expect(result.current.isPreviewRunning).toBe(false);
    });
    expect(result.current.previewResult).toEqual({
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
      metric_columns: ["play_count"],
    });
  });

  it("sets error on SSE error event", async () => {
    mockPreviewStart("preview-op-4");
    mockSSEWithEvents([
      sseFrame("error", { error_message: "Source node failed" }),
    ]);

    const { result } = renderStartedPreview();

    await waitFor(() => {
      expect(result.current.error?.message).toBe("Source node failed");
      expect(result.current.isPreviewRunning).toBe(false);
    });
  });

  it("sets error on mutation failure", async () => {
    server.use(
      http.post("*/api/v1/workflows/:workflowId/preview", () =>
        HttpResponse.json(
          {
            error: {
              code: "VALIDATION_ERROR",
              message: "Workflow has no source node",
            },
          },
          { status: 422 },
        ),
      ),
    );

    const { result } = renderStartedPreview();

    await waitFor(() => {
      expect(result.current.error?.message).toBe("Workflow has no source node");
    });
    expect(result.current.isPreviewRunning).toBe(false);
    expect(connectToSSE).not.toHaveBeenCalled();
  });

  it("clearPreview resets all state", async () => {
    mockPreviewStart("preview-op-5");
    mockSSEWithEvents([
      sseFrame("node_status", {
        node_id: "src_1",
        node_type: "source.liked_tracks",
        status: "completed",
        execution_order: 1,
        total_nodes: 1,
      }),
      sseFrame("preview_complete", {
        output_tracks: [{ rank: 1, title: "X", artists: "Y", isrc: null }],
        node_summaries: [],
      }),
    ]);

    const { result } = renderStartedPreview();

    await waitFor(() => {
      expect(result.current.previewResult?.output_tracks).toHaveLength(1);
    });

    act(() => {
      result.current.clearPreview();
    });

    expect(result.current.previewResult).toBeNull();
    expect(result.current.nodeStatuses.size).toBe(0);
    expect(result.current.error).toBeNull();
    expect(result.current.isPreviewRunning).toBe(false);
  });
});
