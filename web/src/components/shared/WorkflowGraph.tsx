import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  type NodeTypes,
  ReactFlow,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import type { Edge, Node } from "@xyflow/react";
import type { LucideIcon } from "lucide-react";
import {
  ArrowUpDown,
  Database,
  Filter,
  Merge,
  Send,
  Sparkles,
  Target,
} from "lucide-react";
import { useEffect, useState } from "react";
import type { WorkflowTaskDefSchema } from "@/api/generated/model";
import {
  BaseWorkflowNode,
  type WorkflowNodeData,
} from "@/components/workflow/BaseWorkflowNode";
import { layoutWorkflow } from "@/lib/workflow-layout";

const NODE_CONFIG: Record<
  string,
  { Icon: LucideIcon; accentColor: string; label: string }
> = {
  source: {
    Icon: Database,
    accentColor: "oklch(0.7 0.12 250)",
    label: "Source",
  },
  enricher: {
    Icon: Sparkles,
    accentColor: "oklch(0.7 0.14 300)",
    label: "Enricher",
  },
  filter: { Icon: Filter, accentColor: "oklch(0.75 0.14 55)", label: "Filter" },
  sorter: {
    Icon: ArrowUpDown,
    accentColor: "oklch(0.8 0.14 85)",
    label: "Sorter",
  },
  selector: {
    Icon: Target,
    accentColor: "oklch(0.7 0.1 185)",
    label: "Selector",
  },
  combiner: {
    Icon: Merge,
    accentColor: "oklch(0.7 0.14 350)",
    label: "Combiner",
  },
  destination: {
    Icon: Send,
    accentColor: "oklch(0.7 0.14 155)",
    label: "Destination",
  },
};

function createNodeComponent(category: string) {
  const config = NODE_CONFIG[category] ?? NODE_CONFIG.source;
  return ({ data }: { data: WorkflowNodeData }) => (
    <BaseWorkflowNode
      data={data}
      Icon={config.Icon}
      accentColor={config.accentColor}
      label={config.label}
    />
  );
}

const nodeTypes: NodeTypes = Object.fromEntries(
  Object.keys(NODE_CONFIG).map((k) => [k, createNodeComponent(k)]),
);

interface WorkflowGraphProps {
  tasks: WorkflowTaskDefSchema[];
}

export function WorkflowGraph({ tasks }: WorkflowGraphProps) {
  const [nodes, setNodes] = useState<Node[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);
  const [isLayouting, setIsLayouting] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setIsLayouting(true);

    layoutWorkflow(tasks).then((result) => {
      if (!cancelled) {
        setNodes(result.nodes);
        setEdges(result.edges);
        setIsLayouting(false);
      }
    });

    return () => {
      cancelled = true;
    };
  }, [tasks]);

  if (isLayouting) {
    return (
      <div className="flex h-full items-center justify-center text-text-muted">
        <span className="font-display text-sm">Computing layout...</span>
      </div>
    );
  }

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      nodesDraggable={false}
      nodesConnectable={false}
      elementsSelectable={true}
      deleteKeyCode={null}
      fitView
      proOptions={{ hideAttribution: true }}
    >
      <Controls showInteractive={false} />
      <MiniMap
        zoomable
        pannable
        style={{ backgroundColor: "oklch(0.1 0.01 60)" }}
        maskColor="oklch(0.08 0.01 60 / 0.7)"
      />
      <Background
        variant={BackgroundVariant.Dots}
        gap={20}
        size={1}
        color="oklch(0.25 0.01 60)"
      />
    </ReactFlow>
  );
}
