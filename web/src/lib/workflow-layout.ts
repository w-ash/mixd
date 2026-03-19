import type { Edge, Node } from "@xyflow/react";
import type { ELK } from "elkjs/lib/elk-api";

import type { WorkflowTaskDefSchema } from "@/api/generated/model";
import { getNodeCategoryName } from "@/lib/workflow-config";

let elk: ELK | null = null;

async function getELK() {
  if (!elk) {
    const ELK = (await import("elkjs/lib/elk.bundled.js")).default;
    elk = new ELK();
  }
  return elk;
}

const DEFAULT_NODE_WIDTH = 240;
const DEFAULT_NODE_HEIGHT = 80;
const STAGGER_AMPLITUDE = 60;

const EDGE_STYLE = {
  animated: true,
  type: "smart" as const,
  style: { stroke: "oklch(0.5 0.02 60)" },
} as const;

export interface NodeDimension {
  width: number;
  height: number;
}

export function generateNodeId(type: string, existingIds: string[]): string {
  const base = type.replace(/\./g, "_");
  const existing = new Set(existingIds);
  let counter = 1;
  while (existing.has(`${base}_${counter}`)) counter++;
  return `${base}_${counter}`;
}

/**
 * Builds both React Flow edges and ELK edges in a single pass.
 * Validates upstream references against known task IDs to guard
 * against malformed workflow definitions.
 */
export function buildEdges(tasks: WorkflowTaskDefSchema[]): {
  flowEdges: Edge[];
  elkEdges: { id: string; sources: string[]; targets: string[] }[];
} {
  const taskIds = new Set(tasks.map((t) => t.id));
  const flowEdges: Edge[] = [];
  const elkEdges: { id: string; sources: string[]; targets: string[] }[] = [];
  for (const task of tasks) {
    for (const upstream of task.upstream ?? []) {
      if (!taskIds.has(upstream)) continue;
      const id = `e-${upstream}-${task.id}`;
      flowEdges.push({ id, source: upstream, target: task.id, ...EDGE_STYLE });
      elkEdges.push({ id, sources: [upstream], targets: [task.id] });
    }
  }
  return { flowEdges, elkEdges };
}

/**
 * Creates invisible initial nodes for the measurement phase.
 *
 * All nodes are placed at (0, 0) with opacity 0 so React Flow can mount and
 * measure them via ResizeObserver without the user seeing unpositioned nodes.
 */
export function createInitialNodes(tasks: WorkflowTaskDefSchema[]): {
  nodes: Node[];
  edges: Edge[];
} {
  const nodes: Node[] = tasks.map((task) => {
    const category = getNodeCategoryName(task.type);
    return {
      id: task.id,
      type: category,
      position: { x: 0, y: 0 },
      style: { opacity: 0 },
      data: {
        taskId: task.id,
        nodeType: task.type,
        config: task.config ?? {},
      },
    };
  });

  return { nodes, edges: buildEdges(tasks).flowEdges };
}

/**
 * Converts workflow tasks into React Flow nodes + edges with ELK auto-layout.
 *
 * Uses the "layered" algorithm flowing left-to-right. Each task becomes a node;
 * upstream references become edges. ELK computes x/y positions automatically.
 *
 * For linear chains (one node per layer), applies a gentle sine-wave stagger
 * to break up the flat horizontal line.
 *
 * When `nodeDimensions` is provided (from the measurement phase), each node
 * uses its actual rendered size for precise ELK spacing.
 */
export async function layoutWorkflow(
  tasks: WorkflowTaskDefSchema[],
  nodeDimensions?: Map<string, NodeDimension>,
): Promise<{ nodes: Node[]; edges: Edge[] }> {
  if (tasks.length === 0) {
    return { nodes: [], edges: [] };
  }

  const children = tasks.map((task) => {
    const measured = nodeDimensions?.get(task.id);
    return {
      id: task.id,
      width: measured?.width ?? DEFAULT_NODE_WIDTH,
      height: measured?.height ?? DEFAULT_NODE_HEIGHT,
    };
  });

  const { flowEdges, elkEdges } = buildEdges(tasks);

  const elkInstance = await getELK();
  const graph = await elkInstance.layout({
    id: "root",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": "RIGHT",
      "elk.spacing.nodeNode": "80",
      "elk.layered.spacing.nodeNodeBetweenLayers": "180",
      "elk.spacing.edgeNode": "40",
      "elk.spacing.edgeEdge": "30",
      "elk.padding": "[left=50, top=50, right=50, bottom=50]",
      "elk.layered.nodePlacement.strategy": "NETWORK_SIMPLEX",
    },
    children,
    edges: elkEdges,
  });

  const taskMap = new Map(tasks.map((t) => [t.id, t]));
  const layoutChildren = graph.children ?? [];

  // Detect flat layouts: all nodes share the same Y coordinate
  const isFlat =
    layoutChildren.length > 1 &&
    layoutChildren.every((c) => c.y === layoutChildren[0].y);

  const flowNodes: Node[] = layoutChildren.map((child, i) => {
    const task = taskMap.get(child.id);
    const category = task ? getNodeCategoryName(task.type) : "source";
    const measured = nodeDimensions?.get(child.id);
    const w = measured?.width ?? DEFAULT_NODE_WIDTH;
    const h = measured?.height ?? DEFAULT_NODE_HEIGHT;

    // Sine-wave stagger for flat linear chains
    const yOffset = isFlat
      ? Math.sin((i / (layoutChildren.length - 1)) * Math.PI) *
        STAGGER_AMPLITUDE
      : 0;

    return {
      id: child.id,
      type: category,
      position: { x: child.x ?? 0, y: (child.y ?? 0) + yOffset },
      measured: { width: w, height: h },
      data: {
        taskId: task?.id ?? child.id,
        nodeType: task?.type ?? "unknown",
        config: task?.config ?? {},
      },
    };
  });

  return { nodes: flowNodes, edges: flowEdges };
}
