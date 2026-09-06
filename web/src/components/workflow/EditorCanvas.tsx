import {
  Background,
  BackgroundVariant,
  Controls,
  type EdgeTypes,
  MiniMap,
  type NodeTypes,
  ReactFlow,
  useReactFlow,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { SmartBezierEdge } from "@jalez/react-flow-smart-edge";
import type { DragEvent } from "react";
import { createContext, use, useCallback } from "react";

import {
  BaseWorkflowNode,
  type WorkflowNodeData,
} from "#/components/workflow/BaseWorkflowNode";
import { type NodeSchemas, useNodeSchemas } from "#/hooks/useNodeSchemas";
import {
  fallbackNodeHandles,
  miniMapNodeColor,
  NODE_CONFIG,
  type NodeCategoryConfig,
} from "#/lib/workflow-config";
import { useEditorStore } from "#/stores/editor-store";

/**
 * Node components are created once at module scope, so React Flow keeps a
 * stable `nodeTypes` map. They read the schema lookup from context instead of
 * subscribing individually, which keeps the API query to one subscriber.
 */
const NodeSchemasContext = createContext<NodeSchemas | null>(null);

function createEditableNodeComponent(config: NodeCategoryConfig) {
  return function EditableNode({ data }: { data: WorkflowNodeData }) {
    const schemas = use(NodeSchemasContext);
    const handles =
      schemas?.getHandles(data.nodeType) ?? fallbackNodeHandles(data.nodeType);

    return (
      <BaseWorkflowNode
        data={data}
        Icon={config.Icon}
        accentColor={config.accentColor}
        label={config.label}
        configLabels={schemas?.getConfigLabels(data.nodeType)}
        hasInput={handles.input}
        hasOutput={handles.output}
      />
    );
  };
}

const nodeTypes: NodeTypes = Object.fromEntries(
  Object.entries(NODE_CONFIG).map(([category, config]) => [
    category,
    createEditableNodeComponent(config),
  ]),
);

const edgeTypes: EdgeTypes = { smart: SmartBezierEdge };

export function EditorCanvas() {
  const schemas = useNodeSchemas();
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
    <NodeSchemasContext value={schemas}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
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
    </NodeSchemasContext>
  );
}

// Need Node type for onNodeClick
type Node = Parameters<
  NonNullable<React.ComponentProps<typeof ReactFlow>["onNodeClick"]>
>[1];
