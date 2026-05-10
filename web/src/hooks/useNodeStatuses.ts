/**
 * Composable for managing the node status Map shared by workflow execution and preview hooks.
 *
 * Encapsulates the snake_case→camelCase mapping from SSE node_status events
 * into a single source of truth.
 *
 * Same-reference returns when an incoming update is shape-equal to the
 * existing entry — collapses no-op storms (e.g., the REST snapshot poll
 * re-emitting every node every 5s while the run is stalled but unchanged)
 * to zero React reconciliations.
 */

import { useCallback, useState } from "react";

import type { NodeStatus } from "#/lib/sse-types";

export interface UseNodeStatusesReturn {
  nodeStatuses: Map<string, NodeStatus>;
  handleNodeStatusEvent: (data: unknown) => void;
  resetNodeStatuses: () => void;
}

function isSameStatus(a: NodeStatus, b: NodeStatus): boolean {
  return (
    a.nodeId === b.nodeId &&
    a.nodeType === b.nodeType &&
    a.status === b.status &&
    a.executionOrder === b.executionOrder &&
    a.totalNodes === b.totalNodes &&
    a.durationMs === b.durationMs &&
    a.inputTrackCount === b.inputTrackCount &&
    a.outputTrackCount === b.outputTrackCount &&
    a.errorMessage === b.errorMessage
  );
}

export function useNodeStatuses(): UseNodeStatusesReturn {
  const [nodeStatuses, setNodeStatuses] = useState<Map<string, NodeStatus>>(
    new Map(),
  );

  const handleNodeStatusEvent = useCallback((data: unknown) => {
    const d = data as Record<string, unknown>;
    const nodeId = d.node_id as string;
    const incoming: NodeStatus = {
      nodeId,
      nodeType: (d.node_type as string) ?? "",
      status: d.status as NodeStatus["status"],
      executionOrder: (d.execution_order as number) ?? 0,
      totalNodes: (d.total_nodes as number) ?? 0,
      durationMs: d.duration_ms as number | undefined,
      inputTrackCount: d.input_track_count as number | undefined,
      outputTrackCount: d.output_track_count as number | undefined,
      errorMessage: d.error_message as string | undefined,
    };
    setNodeStatuses((prev) => {
      const existing = prev.get(nodeId);
      if (existing && isSameStatus(existing, incoming)) return prev;
      const next = new Map(prev);
      next.set(nodeId, incoming);
      return next;
    });
  }, []);

  const resetNodeStatuses = useCallback(() => {
    setNodeStatuses((prev) => (prev.size === 0 ? prev : new Map()));
  }, []);

  return { nodeStatuses, handleNodeStatusEvent, resetNodeStatuses };
}
