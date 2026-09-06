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

import type { NodeStatus, NodeStatusSource } from "#/lib/sse-types";

export interface UseNodeStatusesReturn {
  nodeStatuses: Map<string, NodeStatus>;
  handleNodeStatusEvent: (source: NodeStatusSource) => void;
  /** Merge a batch of snake_case events in one Map allocation. */
  mergeNodeStatusEvents: (sources: readonly NodeStatusSource[]) => void;
  resetNodeStatuses: () => void;
}

function toNodeStatus(source: NodeStatusSource): NodeStatus {
  return {
    nodeId: source.node_id,
    nodeType: source.node_type,
    // The wire vocabulary is the full RunStatus; the canvas styles the four
    // states a node reports. `cancelled`/`crashed` reach here only from a
    // snapshot of a run stopped mid-node, and render unstyled.
    status: source.status as NodeStatus["status"],
    executionOrder: source.execution_order,
    totalNodes: source.total_nodes,
    durationMs: source.duration_ms ?? undefined,
    inputTrackCount: source.input_track_count ?? undefined,
    outputTrackCount: source.output_track_count ?? undefined,
    errorMessage: source.error_message ?? undefined,
  };
}

export function useNodeStatuses(): UseNodeStatusesReturn {
  const [nodeStatuses, setNodeStatuses] = useState<Map<string, NodeStatus>>(
    new Map(),
  );

  const handleNodeStatusEvent = useCallback((source: NodeStatusSource) => {
    const incoming = toNodeStatus(source);
    setNodeStatuses((prev) => {
      const next = new Map(prev);
      next.set(incoming.nodeId, incoming);
      return next;
    });
  }, []);

  const mergeNodeStatusEvents = useCallback(
    (sources: readonly NodeStatusSource[]) => {
      if (sources.length === 0) return;
      setNodeStatuses((prev) => {
        const next = new Map(prev);
        for (const source of sources) {
          const incoming = toNodeStatus(source);
          next.set(incoming.nodeId, incoming);
        }
        return next;
      });
    },
    [],
  );

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
