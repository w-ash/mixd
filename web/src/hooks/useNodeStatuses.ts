/**
 * Composable for managing the node status Map shared by workflow execution and preview hooks.
 *
 * Encapsulates the snake_case→camelCase mapping from SSE node_status events
 * into a single source of truth.
 */

import { useCallback, useState } from "react";

import type { NodeStatus } from "@/lib/sse-types";

export interface UseNodeStatusesReturn {
  nodeStatuses: Map<string, NodeStatus>;
  handleNodeStatusEvent: (data: unknown) => void;
  resetNodeStatuses: () => void;
}

export function useNodeStatuses(): UseNodeStatusesReturn {
  const [nodeStatuses, setNodeStatuses] = useState<Map<string, NodeStatus>>(
    new Map(),
  );

  const handleNodeStatusEvent = useCallback((data: unknown) => {
    const d = data as Record<string, unknown>;
    setNodeStatuses((prev) => {
      const next = new Map(prev);
      next.set(d.node_id as string, {
        nodeId: d.node_id as string,
        nodeType: (d.node_type as string) ?? "",
        status: d.status as NodeStatus["status"],
        executionOrder: (d.execution_order as number) ?? 0,
        totalNodes: (d.total_nodes as number) ?? 0,
        durationMs: d.duration_ms as number | undefined,
        inputTrackCount: d.input_track_count as number | undefined,
        outputTrackCount: d.output_track_count as number | undefined,
        errorMessage: d.error_message as string | undefined,
      });
      return next;
    });
  }, []);

  const resetNodeStatuses = useCallback(() => {
    setNodeStatuses(new Map());
  }, []);

  return { nodeStatuses, handleNodeStatusEvent, resetNodeStatuses };
}
