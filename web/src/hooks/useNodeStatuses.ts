/**
 * Composable for managing the node status Map shared by workflow execution and preview hooks.
 *
 * Encapsulates the snake_case→camelCase mapping from SSE node_status events
 * into a single source of truth. Tanstack Query's default structuralSharing
 * keeps reference equality on unchanged snapshot responses, so React's
 * built-in render-skip handles the no-op poll case without per-field equality
 * checks here.
 */

import { useCallback, useState } from "react";

import type { NodeStatus } from "#/lib/sse-types";

export interface UseNodeStatusesReturn {
  nodeStatuses: Map<string, NodeStatus>;
  handleNodeStatusEvent: (data: unknown) => void;
  /** Merge a batch of snake_case events in one Map allocation. */
  mergeNodeStatusEvents: (events: readonly unknown[]) => void;
  resetNodeStatuses: () => void;
}

function toNodeStatus(data: unknown): NodeStatus {
  const d = data as Record<string, unknown>;
  return {
    nodeId: d.node_id as string,
    nodeType: (d.node_type as string) ?? "",
    status: d.status as NodeStatus["status"],
    executionOrder: (d.execution_order as number) ?? 0,
    totalNodes: (d.total_nodes as number) ?? 0,
    durationMs: d.duration_ms as number | undefined,
    inputTrackCount: d.input_track_count as number | undefined,
    outputTrackCount: d.output_track_count as number | undefined,
    errorMessage: d.error_message as string | undefined,
  };
}

export function useNodeStatuses(): UseNodeStatusesReturn {
  const [nodeStatuses, setNodeStatuses] = useState<Map<string, NodeStatus>>(
    new Map(),
  );

  const handleNodeStatusEvent = useCallback((data: unknown) => {
    const incoming = toNodeStatus(data);
    setNodeStatuses((prev) => {
      const next = new Map(prev);
      next.set(incoming.nodeId, incoming);
      return next;
    });
  }, []);

  const mergeNodeStatusEvents = useCallback((events: readonly unknown[]) => {
    if (events.length === 0) return;
    setNodeStatuses((prev) => {
      const next = new Map(prev);
      for (const event of events) {
        const incoming = toNodeStatus(event);
        next.set(incoming.nodeId, incoming);
      }
      return next;
    });
  }, []);

  const resetNodeStatuses = useCallback(() => {
    setNodeStatuses((prev) => (prev.size === 0 ? prev : new Map()));
  }, []);

  return {
    nodeStatuses,
    handleNodeStatusEvent,
    mergeNodeStatusEvents,
    resetNodeStatuses,
  };
}
