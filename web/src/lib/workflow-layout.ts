import type { Edge, Node } from "@xyflow/react";
import ELK from "elkjs/lib/elk.bundled.js";

import type { WorkflowTaskDefSchema } from "@/api/generated/model";
import { getCategoryFromNodeType } from "@/components/shared/NodeTypeBadge";

const elk = new ELK();

const NODE_WIDTH = 220;
const NODE_HEIGHT = 80;

/**
 * Converts workflow tasks into React Flow nodes + edges with ELK auto-layout.
 *
 * Uses the "layered" algorithm flowing left-to-right. Each task becomes a node;
 * upstream references become edges. ELK computes x/y positions automatically.
 */
export async function layoutWorkflow(
  tasks: WorkflowTaskDefSchema[],
): Promise<{ nodes: Node[]; edges: Edge[] }> {
  if (tasks.length === 0) {
    return { nodes: [], edges: [] };
  }

  const children = tasks.map((task) => ({
    id: task.id,
    width: NODE_WIDTH,
    height: NODE_HEIGHT,
  }));

  const edges: { id: string; sources: string[]; targets: string[] }[] = [];
  for (const task of tasks) {
    for (const upstream of task.upstream ?? []) {
      edges.push({
        id: `e-${upstream}-${task.id}`,
        sources: [upstream],
        targets: [task.id],
      });
    }
  }

  const graph = await elk.layout({
    id: "root",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": "RIGHT",
      "elk.spacing.nodeNode": "50",
      "elk.layered.spacing.nodeNodeBetweenLayers": "100",
      "elk.padding": "[left=20, top=20, right=20, bottom=20]",
    },
    children,
    edges,
  });

  const taskMap = new Map(tasks.map((t) => [t.id, t]));

  const flowNodes: Node[] = (graph.children ?? []).map((child) => {
    const task = taskMap.get(child.id);
    const category = task ? getCategoryFromNodeType(task.type) : "source";

    return {
      id: child.id,
      type: category,
      position: { x: child.x ?? 0, y: child.y ?? 0 },
      data: {
        taskId: task?.id ?? child.id,
        nodeType: task?.type ?? "unknown",
        config: task?.config ?? {},
      },
    };
  });

  const flowEdges: Edge[] = (graph.edges ?? []).map((edge) => ({
    id: edge.id,
    source: (edge.sources ?? [])[0] ?? "",
    target: (edge.targets ?? [])[0] ?? "",
    animated: true,
    style: { stroke: "oklch(0.5 0.02 60)" },
  }));

  return { nodes: flowNodes, edges: flowEdges };
}
