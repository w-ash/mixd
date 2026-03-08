import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  type NodeTypes,
  ReactFlow,
  useReactFlow,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type { DragEvent } from "react";
import { useCallback } from "react";

import {
  BaseWorkflowNode,
  type WorkflowNodeData,
} from "@/components/workflow/BaseWorkflowNode";
import { NODE_CONFIG } from "@/lib/workflow-config";
import { useEditorStore } from "@/stores/editor-store";

function createEditableNodeComponent(category: string) {
  const config = NODE_CONFIG[category] ?? NODE_CONFIG.source;
  return ({ data }: { data: WorkflowNodeData }) => (
    <BaseWorkflowNode
      data={{ ...data, mode: "edit" }}
      Icon={config.Icon}
      accentColor={config.accentColor}
      label={config.label}
    />
  );
}

const nodeTypes: NodeTypes = Object.fromEntries(
  Object.keys(NODE_CONFIG).map((k) => [k, createEditableNodeComponent(k)]),
);

function miniMapNodeColor(node: { type?: string }) {
  const accent = NODE_CONFIG[node.type ?? ""]?.accentColor;
  return accent
    ? `color-mix(in oklch, ${accent} 35%, oklch(0.15 0.01 60))`
    : "oklch(0.25 0.01 60)";
}

export function EditorCanvas() {
  const nodes = useEditorStore((s) => s.nodes);
  const edges = useEditorStore((s) => s.edges);
  const onNodesChange = useEditorStore((s) => s.onNodesChange);
  const onEdgesChange = useEditorStore((s) => s.onEdgesChange);
  const onConnect = useEditorStore((s) => s.onConnect);
  const addNode = useEditorStore((s) => s.addNode);
  const selectNode = useEditorStore((s) => s.selectNode);
  const removeSelected = useEditorStore((s) => s.removeSelected);

  const { screenToFlowPosition } = useReactFlow();

  const onDragOver = useCallback((event: DragEvent) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
  }, []);

  const onDrop = useCallback(
    (event: DragEvent) => {
      event.preventDefault();
      const nodeType = event.dataTransfer.getData("application/reactflow");
      if (!nodeType) return;

      const position = screenToFlowPosition({
        x: event.clientX,
        y: event.clientY,
      });
      addNode(nodeType, position);
    },
    [screenToFlowPosition, addNode],
  );

  const onNodeClick = useCallback(
    (_event: React.MouseEvent, node: Node) => {
      selectNode(node.id);
    },
    [selectNode],
  );

  const onPaneClick = useCallback(() => {
    selectNode(null);
  }, [selectNode]);

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
      onConnect={onConnect}
      onDragOver={onDragOver}
      onDrop={onDrop}
      onNodeClick={onNodeClick}
      onPaneClick={onPaneClick}
      nodesDraggable
      nodesConnectable
      elementsSelectable
      deleteKeyCode="Delete"
      onDelete={removeSelected}
      fitView
      proOptions={{ hideAttribution: true }}
    >
      <Controls showInteractive={false} />
      <MiniMap
        zoomable
        pannable
        bgColor="oklch(0.1 0.01 60)"
        maskColor="oklch(0.08 0.01 60 / 0.7)"
        nodeColor={miniMapNodeColor}
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

// Need Node type for onNodeClick
type Node = Parameters<
  NonNullable<React.ComponentProps<typeof ReactFlow>["onNodeClick"]>
>[1];
