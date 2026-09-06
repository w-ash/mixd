/**
 * Hook for previewing a workflow via dry-run execution.
 *
 * Triggers preview → connects SSE → processes node_status + preview_complete events.
 * Reuses the same SSE transport as useWorkflowExecution but doesn't create run records.
 */

import { useCallback, useState } from "react";
import type { SseNodePreviewSummary } from "#/api/generated/model";
import {
  usePreviewSavedWorkflowApiV1WorkflowsWorkflowIdPreviewPost,
  usePreviewUnsavedWorkflowApiV1WorkflowsPreviewPost,
} from "#/api/generated/workflows/workflows";
import { useWorkflowSSE } from "#/hooks/useWorkflowSSE";
import { type NodeStatus, SSE_EVENT } from "#/lib/sse-types";
import { toasts } from "#/lib/toasts";
import { useEditorStore } from "#/stores/editor-store";

export interface PreviewTrack {
  rank: number;
  title: string;
  artists: string;
  isrc: string | null;
  metrics?: Record<string, number | string | null>;
}

export type NodePreviewSummary = SseNodePreviewSummary;

export interface PreviewResult {
  output_tracks: PreviewTrack[];
  node_summaries: NodePreviewSummary[];
  metric_columns: string[];
}

export interface UseWorkflowPreviewReturn {
  isPreviewRunning: boolean;
  previewResult: PreviewResult | null;
  nodeStatuses: Map<string, NodeStatus>;
  error: Error | null;
  startPreview: () => void;
  clearPreview: () => void;
}

const PREVIEW_COMPLETION_EVENTS: ReadonlySet<string> = new Set([
  SSE_EVENT.COMPLETE,
  SSE_EVENT.PREVIEW_COMPLETE,
]);

export function useWorkflowPreview(): UseWorkflowPreviewReturn {
  const [previewResult, setPreviewResult] = useState<PreviewResult | null>(
    null,
  );
  const [mutationError, setMutationError] = useState<Error | null>(null);

  const workflowId = useEditorStore((s) => s.workflowId);
  const isDirty = useEditorStore((s) => s.isDirty);
  const toWorkflowDef = useEditorStore((s) => s.toWorkflowDef);

  const unsavedMutation = usePreviewUnsavedWorkflowApiV1WorkflowsPreviewPost();
  const savedMutation =
    usePreviewSavedWorkflowApiV1WorkflowsWorkflowIdPreviewPost();

  const sse = useWorkflowSSE({
    completionEvents: PREVIEW_COMPLETION_EVENTS,
    errorFallbackMessage: "Preview failed",
    onComplete: (_eventType, data) => {
      if (!("output_tracks" in data)) return;
      setPreviewResult({
        // The backend types a preview row as a free-form dict — the metric
        // columns vary with the pipeline — so the row shape is asserted here.
        output_tracks: data.output_tracks as unknown as PreviewTrack[],
        node_summaries: data.node_summaries,
        metric_columns: data.metric_columns,
      });
    },
  });

  const startPreview = useCallback(() => {
    setPreviewResult(null);
    setMutationError(null);

    const handleResponse = (res: { status: number; data: unknown }) => {
      if (res.status === 202) {
        const data = res.data as { operation_id: string };
        sse.start(data.operation_id);
      } else {
        toasts.message("Failed to start preview");
      }
    };

    const handleError = (err: unknown) => {
      setMutationError(
        err instanceof Error ? err : new Error("Failed to start preview"),
      );
      toasts.error("Failed to start preview", err);
    };

    // Preview the saved server definition only when it's actually on disk and
    // unchanged. With pending canvas edits (isDirty) — or a never-saved
    // workflow — preview the canvas itself, so Preview reflects what the user
    // sees rather than a stale server copy.
    if (workflowId !== null && !isDirty) {
      savedMutation.mutate(
        { workflowId },
        { onSuccess: handleResponse, onError: handleError },
      );
    } else {
      const def = toWorkflowDef();
      unsavedMutation.mutate(
        { data: { definition: def } },
        { onSuccess: handleResponse, onError: handleError },
      );
    }
  }, [
    workflowId,
    isDirty,
    toWorkflowDef,
    savedMutation.mutate,
    unsavedMutation.mutate,
    sse.start,
  ]);

  const clearPreview = useCallback(() => {
    sse.reset();
    setPreviewResult(null);
    setMutationError(null);
  }, [sse.reset]);

  return {
    isPreviewRunning:
      savedMutation.isPending || unsavedMutation.isPending || sse.isRunning,
    previewResult,
    nodeStatuses: sse.nodeStatuses,
    error: mutationError ?? sse.error,
    startPreview,
    clearPreview,
  };
}
