import type {
  Edge,
  Node,
  OnConnect,
  OnEdgesChange,
  OnNodesChange,
  XYPosition,
} from "@xyflow/react";
import { addEdge, applyEdgeChanges, applyNodeChanges } from "@xyflow/react";
import { create } from "zustand";

import type {
  WorkflowDefSchema,
  WorkflowTaskDefSchema,
} from "@/api/generated/model";
import { getNodeCategoryName } from "@/lib/workflow-config";
import {
  buildEdges,
  generateNodeId,
  layoutWorkflow,
} from "@/lib/workflow-layout";

const HISTORY_LIMIT = 50;

interface HistoryEntry {
  nodes: Node[];
  edges: Edge[];
}

interface EditorState {
  // React Flow state
  nodes: Node[];
  edges: Edge[];

  // History (undo/redo)
  past: HistoryEntry[];
  future: HistoryEntry[];

  // Metadata
  workflowId: number | null;
  workflowName: string;
  workflowDescription: string;
  isDirty: boolean;
  selectedNodeId: string | null;

  // Actions - React Flow
  onNodesChange: OnNodesChange;
  onEdgesChange: OnEdgesChange;
  onConnect: OnConnect;

  // Actions - Editor
  addNode: (
    type: string,
    position: XYPosition,
    config?: Record<string, unknown>,
  ) => void;
  removeSelected: () => void;
  updateNodeConfig: (nodeId: string, config: Record<string, unknown>) => void;
  updateNodeTaskId: (nodeId: string, taskId: string) => void;
  selectNode: (nodeId: string | null) => void;

  // Actions - History
  undo: () => void;
  redo: () => void;
  pushHistory: () => void;

  // Actions - Persistence
  loadWorkflow: (def: WorkflowDefSchema, workflowId?: number) => void;
  toWorkflowDef: () => WorkflowDefSchema;
  resetDirty: () => void;
  setName: (name: string) => void;
  setDescription: (desc: string) => void;
  setNodes: (nodes: Node[]) => void;
  setEdges: (edges: Edge[]) => void;
}

export const useEditorStore = create<EditorState>()((set, get) => ({
  // Initial state
  nodes: [],
  edges: [],
  past: [],
  future: [],
  workflowId: null,
  workflowName: "Untitled Workflow",
  workflowDescription: "",
  isDirty: false,
  selectedNodeId: null,

  // React Flow event handlers
  onNodesChange: (changes) => {
    // Position/dimension changes happen on every drag pixel — don't mark dirty
    const isStructural = changes.some(
      (c) => c.type !== "position" && c.type !== "dimensions",
    );
    set((state) => ({
      nodes: applyNodeChanges(changes, state.nodes),
      isDirty: state.isDirty || isStructural,
    }));
  },

  onEdgesChange: (changes) => {
    set((state) => ({
      edges: applyEdgeChanges(changes, state.edges),
      isDirty: true,
    }));
  },

  onConnect: (connection) => {
    // No self-loops
    if (connection.source === connection.target) return;

    const { edges } = get();
    // No duplicate edges
    const duplicate = edges.some(
      (e) => e.source === connection.source && e.target === connection.target,
    );
    if (duplicate) return;

    get().pushHistory();
    set((state) => ({
      edges: addEdge(connection, state.edges),
      isDirty: true,
    }));
  },

  // Editor actions
  addNode: (type, position, config) => {
    const { nodes } = get();
    const existingIds = nodes.map((n) => n.id);
    const taskId = generateNodeId(type, existingIds);
    const category = getNodeCategoryName(type);

    const newNode: Node = {
      id: taskId,
      type: category,
      position,
      data: {
        taskId,
        nodeType: type,
        config: config ?? {},
      },
    };

    get().pushHistory();
    set((state) => ({
      nodes: [...state.nodes, newNode],
      isDirty: true,
    }));
  },

  removeSelected: () => {
    get().pushHistory();
    set((state) => {
      const selectedIds = new Set(
        state.nodes.filter((n) => n.selected).map((n) => n.id),
      );
      return {
        nodes: state.nodes.filter((n) => !n.selected),
        edges: state.edges.filter(
          (e) => !selectedIds.has(e.source) && !selectedIds.has(e.target),
        ),
        isDirty: true,
        selectedNodeId: selectedIds.has(state.selectedNodeId ?? "")
          ? null
          : state.selectedNodeId,
      };
    });
  },

  updateNodeConfig: (nodeId, config) => {
    set((state) => ({
      nodes: state.nodes.map((n) =>
        n.id === nodeId ? { ...n, data: { ...n.data, config } } : n,
      ),
      isDirty: true,
    }));
  },

  updateNodeTaskId: (nodeId, taskId) => {
    set((state) => ({
      nodes: state.nodes.map((n) =>
        n.id === nodeId ? { ...n, id: taskId, data: { ...n.data, taskId } } : n,
      ),
      edges: state.edges.map((e) => {
        const newSource = e.source === nodeId ? taskId : e.source;
        const newTarget = e.target === nodeId ? taskId : e.target;
        return {
          ...e,
          id: `e-${newSource}-${newTarget}`,
          source: newSource,
          target: newTarget,
        };
      }),
      selectedNodeId:
        state.selectedNodeId === nodeId ? taskId : state.selectedNodeId,
      isDirty: true,
    }));
  },

  selectNode: (nodeId) => {
    set({ selectedNodeId: nodeId });
  },

  // History
  pushHistory: () => {
    set((state) => ({
      past: [
        ...state.past.slice(-(HISTORY_LIMIT - 1)),
        { nodes: state.nodes, edges: state.edges },
      ],
      future: [],
    }));
  },

  undo: () => {
    const { past, future, nodes, edges } = get();
    if (past.length === 0) return;

    const previous = past[past.length - 1];
    set({
      past: past.slice(0, -1),
      future: [{ nodes, edges }, ...future],
      nodes: previous.nodes,
      edges: previous.edges,
      isDirty: true,
    });
  },

  redo: () => {
    const { past, future, nodes, edges } = get();
    if (future.length === 0) return;

    const next = future[0];
    set({
      future: future.slice(1),
      past: [...past, { nodes, edges }],
      nodes: next.nodes,
      edges: next.edges,
      isDirty: true,
    });
  },

  // Persistence
  loadWorkflow: (def, workflowId) => {
    const tasks = def.tasks ?? [];

    const nodes: Node[] = tasks.map((task) => ({
      id: task.id,
      type: getNodeCategoryName(task.type),
      position: { x: 0, y: 0 },
      style: { opacity: 0 },
      data: {
        taskId: task.id,
        nodeType: task.type,
        config: task.config ?? {},
      },
    }));

    const edges = buildEdges(tasks).flowEdges;

    set({
      nodes,
      edges,
      workflowId: workflowId ?? null,
      workflowName: def.name,
      workflowDescription: def.description ?? "",
      isDirty: false,
      past: [],
      future: [],
      selectedNodeId: null,
    });

    // Run async ELK layout and update positions
    layoutWorkflow(tasks).then((result) => {
      set({ nodes: result.nodes, edges: result.edges });
    });
  },

  toWorkflowDef: () => {
    const { nodes, edges, workflowId, workflowName, workflowDescription } =
      get();

    const tasks: WorkflowTaskDefSchema[] = nodes.map((node) => ({
      id: node.data.taskId as string,
      type: node.data.nodeType as string,
      config: (node.data.config as Record<string, unknown>) ?? {},
      upstream: edges.filter((e) => e.target === node.id).map((e) => e.source),
    }));

    return {
      id: workflowId ? String(workflowId) : "new-workflow",
      name: workflowName,
      description: workflowDescription,
      version: "1.0",
      tasks,
    };
  },

  resetDirty: () => {
    set({ isDirty: false });
  },

  setName: (name) => {
    set({ workflowName: name, isDirty: true });
  },

  setDescription: (desc) => {
    set({ workflowDescription: desc, isDirty: true });
  },

  setNodes: (nodes) => {
    set({ nodes });
  },

  setEdges: (edges) => {
    set({ edges });
  },
}));
