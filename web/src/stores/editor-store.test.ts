import type { Edge, Node } from "@xyflow/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ConfigFieldSchema } from "#/api/generated/model";

import {
  type NodeSchemaMap,
  selectIncomingUpstreams,
  useEditorStore,
} from "./editor-store";

vi.mock("#/lib/workflow-layout", () => ({
  layoutWorkflow: vi.fn().mockResolvedValue({ nodes: [], edges: [] }),
  buildEdges: vi.fn().mockReturnValue({ flowEdges: [] }),
  generateNodeId: vi.fn().mockReturnValue("node_1"),
}));

function node(
  id: string,
  nodeType: string,
  config: Record<string, unknown> = {},
): Node {
  return {
    id,
    type: "source",
    position: { x: 0, y: 0 },
    data: { taskId: id, nodeType, config },
  };
}

function edge(source: string, target: string): Edge {
  return { id: `e-${source}-${target}`, source, target };
}

describe("selectIncomingUpstreams", () => {
  const nodes = [
    node("src_1", "source.liked_tracks"),
    node("src_2", "source.played_tracks"),
    node("merge_1", "combiner.merge_playlists"),
  ];
  const edges = [
    edge("src_1", "merge_1"),
    edge("src_2", "merge_1"),
    edge("merge_1", "dest_1"),
  ];

  it("returns incoming edge sources with display labels, in edge order", () => {
    expect(selectIncomingUpstreams(nodes, edges, "merge_1")).toEqual([
      { id: "src_1", label: "liked tracks" },
      { id: "src_2", label: "played tracks" },
    ]);
  });

  it("falls back to the task id when the source node is unknown", () => {
    expect(selectIncomingUpstreams([], edges, "merge_1")).toEqual([
      { id: "src_1", label: "src_1" },
      { id: "src_2", label: "src_2" },
    ]);
  });

  it("is empty for a source node and for no selection", () => {
    expect(selectIncomingUpstreams(nodes, edges, "src_1")).toEqual([]);
    expect(selectIncomingUpstreams(nodes, edges, null)).toEqual([]);
  });
});

const TASK_REF = (key: string): ConfigFieldSchema => ({
  key,
  label: key,
  field_type: "task_ref",
});

const SCHEMAS: NodeSchemaMap = new Map([
  [
    "filter.by_tracks",
    [
      TASK_REF("exclusion_source"),
      TASK_REF("primary_input"),
      { key: "limit", label: "Limit", field_type: "number" },
    ],
  ],
  ["sorter.by_metric", [TASK_REF("primary_input")]],
]);

function configOf(id: string): Record<string, unknown> {
  const n = useEditorStore.getState().nodes.find((n) => n.id === id);
  return n?.data.config as Record<string, unknown>;
}

describe("task_ref upkeep on structural edits", () => {
  beforeEach(() => {
    useEditorStore.getState().resetWorkflow();
    useEditorStore.setState({
      nodeSchemas: SCHEMAS,
      nodes: [
        node("src_1", "source.liked_tracks"),
        node("src_2", "source.played_tracks"),
        node("filter_1", "filter.by_tracks", {
          primary_input: "src_1",
          exclusion_source: "src_2",
          limit: 10,
        }),
        node("sort_1", "sorter.by_metric", { primary_input: "filter_1" }),
      ],
      edges: [
        edge("src_1", "filter_1"),
        edge("src_2", "filter_1"),
        edge("filter_1", "sort_1"),
      ],
    });
  });

  it("clears a task_ref when the edge it named is removed", () => {
    useEditorStore
      .getState()
      .onEdgesChange([{ type: "remove", id: "e-src_1-filter_1" }]);

    expect(configOf("filter_1")).toEqual({
      exclusion_source: "src_2",
      limit: 10,
    });
    expect(configOf("sort_1")).toEqual({ primary_input: "filter_1" });
  });

  it("leaves nodes untouched when no edge is removed", () => {
    const before = useEditorStore.getState().nodes;
    useEditorStore
      .getState()
      .onEdgesChange([
        { type: "select", id: "e-src_1-filter_1", selected: true },
      ]);

    expect(useEditorStore.getState().nodes).toBe(before);
  });

  it("rewrites task_refs when an upstream task is renamed", () => {
    useEditorStore.getState().updateNodeTaskId("src_1", "liked");

    expect(configOf("filter_1")).toEqual({
      primary_input: "liked",
      exclusion_source: "src_2",
      limit: 10,
    });
    expect(useEditorStore.getState().edges).toContainEqual(
      edge("liked", "filter_1"),
    );
  });

  it("clears task_refs to a node that is deleted", () => {
    useEditorStore.setState((s) => ({
      nodes: s.nodes.map((n) =>
        n.id === "src_2" ? { ...n, selected: true } : n,
      ),
    }));
    useEditorStore.getState().removeSelected();

    expect(configOf("filter_1")).toEqual({
      primary_input: "src_1",
      limit: 10,
    });
  });

  it("does nothing without schemas to key off", () => {
    useEditorStore.setState({ nodeSchemas: new Map() });
    useEditorStore
      .getState()
      .onEdgesChange([{ type: "remove", id: "e-src_1-filter_1" }]);

    expect(configOf("filter_1")).toEqual({
      primary_input: "src_1",
      exclusion_source: "src_2",
      limit: 10,
    });
  });
});
