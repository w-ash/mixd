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
} from "@/api/generated/workflows/workflows";
import { useNodeStatuses } from "@/hooks/useNodeStatuses";
import { useSSEConnection } from "@/hooks/useSSEConnection";
import type { NodeStatus } from "@/lib/sse-types";
import { useEditorStore } from "@/stores/editor-store";

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

export function useWorkflowPreview(): UseWorkflowPreviewReturn {
  const [operationId, setOperationId] = useState<string | null>(null);
  const [isPreviewRunning, setIsPreviewRunning] = useState(false);
  const [previewResult, setPreviewResult] = useState<PreviewResult | null>(
    null,
  );
  const [domainError, setDomainError] = useState<Error | null>(null);

  const workflowId = useEditorStore((s) => s.workflowId);
  const toWorkflowDef = useEditorStore((s) => s.toWorkflowDef);

  const unsavedMutation = usePreviewUnsavedWorkflowApiV1WorkflowsPreviewPost();
  const savedMutation =
    usePreviewSavedWorkflowApiV1WorkflowsWorkflowIdPreviewPost();

  const { nodeStatuses, handleNodeStatusEvent, resetNodeStatuses } =
    useNodeStatuses();

  const { error: sseError, disconnect } = useSSEConnection(operationId, {
    onEvent(eventType, data) {
      switch (eventType) {
        case "node_status":
          handleNodeStatusEvent(data);
          break;

        case "preview_complete": {
          const d = data as Record<string, unknown>;
          setPreviewResult({
            output_tracks: (d.output_tracks as PreviewTrack[]) ?? [],
            node_summaries: (d.node_summaries as NodePreviewSummary[]) ?? [],
            metric_columns: (d.metric_columns as string[]) ?? [],
          });
          setIsPreviewRunning(false);
          disconnect();
          break;
        }

        case "complete":
          setIsPreviewRunning(false);
          disconnect();
          break;

        case "error": {
          const d = data as Record<string, unknown>;
          setIsPreviewRunning(false);
          setDomainError(
            new Error((d.error_message as string) ?? "Preview failed"),
          );
          disconnect();
          break;
        }
      }
    },
  });

  const startPreview = useCallback(() => {
    setDomainError(null);
    setPreviewResult(null);
    resetNodeStatuses();
    setIsPreviewRunning(true);

    const handleResponse = (res: { status: number; data: unknown }) => {
      if (res.status === 202) {
        const data = res.data as { operation_id: string };
        setOperationId(data.operation_id);
      } else {
        toast.error("Failed to start preview");
        setIsPreviewRunning(false);
      }
    };

    const handleError = (err: unknown) => {
      setDomainError(
        err instanceof Error ? err : new Error("Failed to start preview"),
      );
      setIsPreviewRunning(false);
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
    savedMutation,
    unsavedMutation,
    resetNodeStatuses,
  ]);

  const clearPreview = useCallback(() => {
    disconnect();
    setPreviewResult(null);
    resetNodeStatuses();
    setDomainError(null);
    setIsPreviewRunning(false);
    setOperationId(null);
  }, [disconnect, resetNodeStatuses]);

  return {
    isPreviewRunning,
    previewResult,
    nodeStatuses,
    error: domainError ?? sseError,
    startPreview,
    clearPreview,
  };
}
