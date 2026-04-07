/**
 * Hook for previewing a workflow via dry-run execution.
 *
 * Triggers preview → connects SSE → processes node_status + preview_complete events.
 * Reuses the same SSE transport as useWorkflowExecution but doesn't create run records.
 */

import { useCallback, useState } from "react";
import { toast } from "sonner";

import {
  usePreviewSavedWorkflowApiV1WorkflowsWorkflowIdPreviewPost,
  usePreviewUnsavedWorkflowApiV1WorkflowsPreviewPost,
} from "#/api/generated/workflows/workflows";
import { useWorkflowSSE } from "#/hooks/useWorkflowSSE";
import type { NodeStatus } from "#/lib/sse-types";
import { useEditorStore } from "#/stores/editor-store";

export interface PreviewTrack {
  rank: number;
  title: string;
  artists: string;
  isrc: string | null;
  metrics?: Record<string, number | string | null>;
}

export interface NodePreviewSummary {
  node_id: string;
  node_type: string;
  track_count: number;
  sample_titles: string[];
}

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
  "complete",
  "preview_complete",
]);

export function useWorkflowPreview(): UseWorkflowPreviewReturn {
  const [previewResult, setPreviewResult] = useState<PreviewResult | null>(
    null,
  );
  const [mutationError, setMutationError] = useState<Error | null>(null);

  const workflowId = useEditorStore((s) => s.workflowId);
  const toWorkflowDef = useEditorStore((s) => s.toWorkflowDef);

  const unsavedMutation = usePreviewUnsavedWorkflowApiV1WorkflowsPreviewPost();
  const savedMutation =
    usePreviewSavedWorkflowApiV1WorkflowsWorkflowIdPreviewPost();

  const sse = useWorkflowSSE({
    completionEvents: PREVIEW_COMPLETION_EVENTS,
    errorFallbackMessage: "Preview failed",
    onComplete: (_eventType, data) => {
      const d = data as Record<string, unknown>;
      if (d.output_tracks !== undefined) {
        setPreviewResult({
          output_tracks: (d.output_tracks as PreviewTrack[]) ?? [],
          node_summaries: (d.node_summaries as NodePreviewSummary[]) ?? [],
          metric_columns: (d.metric_columns as string[]) ?? [],
        });
      }
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
        toast.error("Failed to start preview");
      }
    };

    const handleError = (err: unknown) => {
      const error =
        err instanceof Error ? err : new Error("Failed to start preview");
      setMutationError(error);
      toast.error("Failed to start preview");
    };

    if (workflowId !== null) {
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
