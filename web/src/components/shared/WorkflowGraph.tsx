import {
  Background,
  BackgroundVariant,
  ControlButton,
  Controls,
  MiniMap,
  type NodeTypes,
  ReactFlow,
  ReactFlowProvider,
  useNodesInitialized,
  useReactFlow,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { SmartBezierEdge } from "@jalez/react-flow-smart-edge";
import type { Edge, EdgeTypes, Node } from "@xyflow/react";
import { Maximize2, Minimize2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { WorkflowTaskDefSchema } from "#/api/generated/model";
import {
  BaseWorkflowNode,
  type WorkflowNodeData,
} from "#/components/workflow/BaseWorkflowNode";
import type { NodeStatus } from "#/lib/sse-types";
import { miniMapNodeColor, NODE_CONFIG } from "#/lib/workflow-config";
import {
  createInitialNodes,
  layoutWorkflow,
  type NodeDimension,
} from "#/lib/workflow-layout";

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

const edgeTypes: EdgeTypes = { smart: SmartBezierEdge };

type LayoutPhase = "measuring" | "layouting" | "done";

const MEASUREMENT_TIMEOUT_MS = 2000;

import type { DiffStatus } from "#/lib/workflow-diff";

interface WorkflowGraphProps {
  tasks: WorkflowTaskDefSchema[];
  nodeStatuses?: Map<string, NodeStatus>;
  /** Optional diff highlight map: node ID → diff status for coloring */
  highlightMap?: Map<string, DiffStatus>;
}

export function WorkflowGraph(props: WorkflowGraphProps) {
  return (
    <ReactFlowProvider>
      <WorkflowGraphInner {...props} />
    </ReactFlowProvider>
  );
}

function WorkflowGraphInner({
  tasks,
  nodeStatuses,
  highlightMap,
}: WorkflowGraphProps) {
  const [nodes, setNodes] = useState<Node[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);
  const [phase, setPhase] = useState<LayoutPhase>("measuring");
  const { getNodes, fitView } = useReactFlow();
  const nodesInitialized = useNodesInitialized();

  // Track current task set and phase via refs to avoid stale closures
  const tasksRef = useRef(tasks);
  tasksRef.current = tasks;
  const phaseRef = useRef(phase);
  phaseRef.current = phase;

  // Fullscreen toggle
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    if (!expanded) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setExpanded(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [expanded]);

  const toggleExpanded = useCallback(() => {
    setExpanded((v) => !v);
    // Re-center after the container resizes from the toggle
    requestAnimationFrame(() => fitView({ duration: 200 }));
  }, [fitView]);

  // Phase 1: Tasks change → create invisible measurement nodes
  useEffect(() => {
    if (tasks.length === 0) {
      setNodes([]);
      setEdges([]);
      setPhase("done");
      return;
    }
    const { nodes, edges } = createInitialNodes(tasks);
    setNodes(nodes);
    setEdges(edges);
    setPhase("measuring");
  }, [tasks]);

  // Phase 2: Nodes measured → run ELK with real dimensions
  const runLayout = useCallback(
    (useFallback: boolean) => {
      if (phaseRef.current !== "measuring") return;
      setPhase("layouting");

      const dimensions = new Map<string, NodeDimension>();
      if (!useFallback) {
        for (const node of getNodes()) {
          if (node.measured?.width && node.measured?.height) {
            dimensions.set(node.id, {
              width: node.measured.width,
              height: node.measured.height,
            });
          }
        }
      }

      const currentTasks = tasksRef.current;
      layoutWorkflow(currentTasks, dimensions)
        .then((result) => {
          // Guard against stale results if tasks changed while layouting
          if (tasksRef.current !== currentTasks) return;
          setNodes(result.nodes);
          setEdges(result.edges);
          setPhase("done");
          fitView({ duration: 200 });
        })
        .catch(() => {
          setPhase("done");
        });
    },
    [getNodes, fitView],
  );

  // Trigger layout when measurement completes
  useEffect(() => {
    if (phase === "measuring" && nodesInitialized && tasks.length > 0) {
      runLayout(false);
    }
  }, [phase, nodesInitialized, tasks.length, runLayout]);

  // Fallback: if measurement doesn't complete within timeout, use defaults
  useEffect(() => {
    if (phase !== "measuring" || tasks.length === 0) return;
    const timer = setTimeout(() => runLayout(true), MEASUREMENT_TIMEOUT_MS);
    return () => clearTimeout(timer);
  }, [phase, tasks.length, runLayout]);

  // Merge execution status and diff highlights into node data
  const displayNodes = useMemo(() => {
    if (!nodeStatuses?.size && !highlightMap?.size) return nodes;
    return nodes.map((node) => {
      const status = nodeStatuses?.get(node.id);
      const diff = highlightMap?.get(node.id);
      if (!status && !diff) return node;
      return {
        ...node,
        data: {
          ...node.data,
          ...(status && {
            executionStatus: status.status,
            inputTrackCount: status.inputTrackCount,
            outputTrackCount: status.outputTrackCount,
            errorMessage: status.errorMessage,
          }),
          ...(diff && { diffStatus: diff }),
        },
      };
    });
  }, [nodes, nodeStatuses, highlightMap]);

  return (
    <div
      className={
        expanded
          ? "fixed inset-0 z-50 h-screen w-screen bg-surface-sunken"
          : "relative h-full w-full"
      }
    >
      {phase !== "done" && (
        <div className="absolute inset-0 z-10 flex items-center justify-center bg-surface-sunken/80">
          <span className="font-display text-sm text-text-muted">
            Computing layout...
          </span>
        </div>
      )}
      <ReactFlow
        nodes={displayNodes}
        edges={edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={true}
        deleteKeyCode={null}
        fitView
        proOptions={{ hideAttribution: true }}
      >
        <Controls showInteractive={false}>
          <ControlButton
            onClick={toggleExpanded}
            title={expanded ? "Exit fullscreen" : "Fullscreen"}
          >
            {expanded ? <Minimize2 /> : <Maximize2 />}
          </ControlButton>
        </Controls>
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
    </div>
  );
}
