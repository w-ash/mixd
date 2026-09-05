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
  ConfigFieldSchema,
  WorkflowDefSchemaInput,
  WorkflowTaskDefSchemaInput,
} from "#/api/generated/model";
import { taskRefKeys } from "#/lib/config-fields";
import { formatNodeTypeName, getNodeCategoryName } from "#/lib/workflow-config";
import {
  buildEdges,
  generateNodeId,
  layoutWorkflow,
} from "#/lib/workflow-layout";

const HISTORY_LIMIT = 50;

interface HistoryEntry {
  nodes: Node[];
  edges: Edge[];
}

/** Config field declarations by node type, as served by the node-types API. */
export type NodeSchemaMap = ReadonlyMap<string, ConfigFieldSchema[]>;

interface EditorState {
  // React Flow state
  nodes: Node[];
  edges: Edge[];

  // History (undo/redo)
  past: HistoryEntry[];
  future: HistoryEntry[];

  // Metadata
  workflowId: string | null;
  workflowName: string;
  workflowDescription: string;
  isDirty: boolean;
  selectedNodeId: string | null;

  /** Field declarations the structural edits consult (see `mapTaskRefs`). */
  nodeSchemas: NodeSchemaMap;

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
  setNodeSchemas: (schemas: NodeSchemaMap) => void;

  // Actions - History
  undo: () => void;
  redo: () => void;
  pushHistory: () => void;

  // Actions - Persistence
  loadWorkflow: (def: WorkflowDefSchemaInput, workflowId?: string) => void;
  resetWorkflow: () => void;
  toWorkflowDef: () => WorkflowDefSchemaInput;
  resetDirty: () => void;
  setName: (name: string) => void;
  setDescription: (desc: string) => void;
  setNodes: (nodes: Node[]) => void;
  setEdges: (edges: Edge[]) => void;
}

/** One upstream task of a node, as offered by `task_ref` pickers. */
export interface UpstreamRef {
  id: string;
  label: string;
}

/**
 * Upstream tasks of `nodeId`: the sources of its incoming edges, in edge
 * order. `label` is the node's display name (the task id when the node is
 * unknown). Pure, so callers decide how reactively to read `nodes`/`edges`.
 */
export function selectIncomingUpstreams(
  nodes: readonly Node[],
  edges: readonly Edge[],
  nodeId: string | null,
): UpstreamRef[] {
  if (!nodeId) return [];
  return edges
    .filter((e) => e.target === nodeId)
    .map((e) => {
      const source = nodes.find((n) => n.id === e.source);
      const nodeType = source?.data.nodeType;
      return {
        id: e.source,
        label:
          typeof nodeType === "string"
            ? formatNodeTypeName(nodeType)
            : e.source,
      };
    });
}

/**
 * Nodes with every `task_ref` config value passed through `mapRef`, which
 * receives the current value and the node's upstream ids (edge sources) and
 * returns the value to keep, or `undefined` to drop the key. Fields are found
 * by their `task_ref` declaration in `schemas`, never by name. Returns
 * `nodes` itself when nothing changed, so React Flow does not re-render.
 */
function mapTaskRefs(
  nodes: Node[],
  edges: readonly Edge[],
  schemas: NodeSchemaMap,
  mapRef: (ref: unknown, upstreamIds: ReadonlySet<string>) => unknown,
): Node[] {
  let changed = false;
  const next = nodes.map((node) => {
    const keys = taskRefKeys(schemas.get(node.data.nodeType as string) ?? []);
    const config = node.data.config as Record<string, unknown>;
    const present = keys.filter((k) => config[k] != null);
    if (present.length === 0) return node;

    const upstreamIds = new Set(
      edges.filter((e) => e.target === node.id).map((e) => e.source),
    );
    const mapped = { ...config };
    let nodeChanged = false;
    for (const key of present) {
      const value = mapRef(config[key], upstreamIds);
      if (value === config[key]) continue;
      if (value === undefined) delete mapped[key];
      else mapped[key] = value;
      nodeChanged = true;
    }
    if (!nodeChanged) return node;
    changed = true;
    return { ...node, data: { ...node.data, config: mapped } };
  });
  return changed ? next : nodes;
}

/** Drops a `task_ref` whose task is no longer one of the node's upstreams. */
const dropStaleRef = (ref: unknown, upstreamIds: ReadonlySet<string>) =>
  typeof ref === "string" && upstreamIds.has(ref) ? ref : undefined;

/** The blank-canvas state, fresh each call (new array refs). Single source for
 *  the store's initial values and `resetWorkflow`, so they can't drift apart. */
function initialWorkflowState(): Pick<
  EditorState,
  | "nodes"
  | "edges"
  | "past"
  | "future"
  | "workflowId"
  | "workflowName"
  | "workflowDescription"
  | "isDirty"
  | "selectedNodeId"
> {
  return {
    nodes: [],
    edges: [],
    past: [],
    future: [],
    workflowId: null,
    workflowName: "Untitled Workflow",
    workflowDescription: "",
    isDirty: false,
    selectedNodeId: null,
  };
}

export const useEditorStore = create<EditorState>()((set, get) => ({
  // Initial state
  ...initialWorkflowState(),
  nodeSchemas: new Map(),

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
    set((state) => {
      const edges = applyEdgeChanges(changes, state.edges);
      // A removed edge orphans any task_ref that named its source; the panel
      // hides such a field, so scrub it here rather than let the save fail.
      const nodes = changes.some((c) => c.type === "remove")
        ? mapTaskRefs(state.nodes, edges, state.nodeSchemas, dropStaleRef)
        : state.nodes;
      return { nodes, edges, isDirty: true };
    });
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
      const edges = state.edges.filter(
        (e) => !selectedIds.has(e.source) && !selectedIds.has(e.target),
      );
      return {
        nodes: mapTaskRefs(
          state.nodes.filter((n) => !n.selected),
          edges,
          state.nodeSchemas,
          dropStaleRef,
        ),
        edges,
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
    set((state) => {
      const edges = state.edges.map((e) => {
        const newSource = e.source === nodeId ? taskId : e.source;
        const newTarget = e.target === nodeId ? taskId : e.target;
        return {
          ...e,
          id: `e-${newSource}-${newTarget}`,
          source: newSource,
          target: newTarget,
        };
      });
      const renamed = state.nodes.map((n) =>
        n.id === nodeId ? { ...n, id: taskId, data: { ...n.data, taskId } } : n,
      );
      return {
        // Downstream task_refs follow the rename so they keep pointing here.
        nodes: mapTaskRefs(renamed, edges, state.nodeSchemas, (ref) =>
          ref === nodeId ? taskId : ref,
        ),
        edges,
        selectedNodeId:
          state.selectedNodeId === nodeId ? taskId : state.selectedNodeId,
        isDirty: true,
      };
    });
  },

  selectNode: (nodeId) => {
    set({ selectedNodeId: nodeId });
  },

  setNodeSchemas: (schemas) => {
    set({ nodeSchemas: schemas });
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

    // Run async ELK layout and update positions. Layout is best-effort: if ELK
    // rejects (e.g. a malformed imported graph that slipped the parse guard),
    // reveal the nodes where they were seeded instead of leaving a blank canvas
    // or an unhandled rejection.
    layoutWorkflow(tasks)
      .then((result) => {
        set({ nodes: result.nodes, edges: result.edges });
      })
      .catch(() => {
        set((state) => ({
          nodes: state.nodes.map((n) => ({
            ...n,
            style: { ...n.style, opacity: 1 },
          })),
        }));
      });
  },

  resetWorkflow: () => {
    set(initialWorkflowState());
  },

  toWorkflowDef: () => {
    const { nodes, edges, workflowId, workflowName, workflowDescription } =
      get();

    const tasks: WorkflowTaskDefSchemaInput[] = nodes.map((node) => ({
      id: node.data.taskId as string,
      type: node.data.nodeType as string,
      config: (node.data.config as WorkflowTaskDefSchemaInput["config"]) ?? {},
      upstream: edges.filter((e) => e.target === node.id).map((e) => e.source),
    }));

    return {
      id: workflowId ?? "new-workflow",
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
