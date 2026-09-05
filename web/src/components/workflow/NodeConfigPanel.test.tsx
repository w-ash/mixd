import type { Node } from "@xyflow/react";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type {
  ConfigFieldSchema,
  NodeTypeInfoSchema,
} from "#/api/generated/model";

// Mock smart-edge (transitive dep via WorkflowGraph chain)
vi.mock("@jalez/react-flow-smart-edge", () => ({
  SmartBezierEdge: () => null,
}));

vi.mock("@xyflow/react", () => ({
  Handle: () => null,
  Position: { Left: "left", Right: "right" },
  ReactFlowProvider: ({ children }: { children: React.ReactNode }) => children,
  useReactFlow: () => ({ fitView: vi.fn() }),
  useNodesInitialized: () => false,
}));

vi.mock("#/lib/workflow-layout", () => ({
  layoutWorkflow: vi.fn().mockResolvedValue({ nodes: [], edges: [] }),
  buildEdges: vi.fn().mockReturnValue([]),
  generateNodeId: vi.fn().mockReturnValue("node_1"),
  createInitialNodes: vi.fn().mockReturnValue({ nodes: [], edges: [] }),
}));

import { useEditorStore } from "#/stores/editor-store";
import { server } from "#/test/setup";
import {
  renderWithProviders,
  screen,
  userEvent,
  waitFor,
} from "#/test/test-utils";
import { NO_UPSTREAM_MESSAGE } from "./fields/TaskRefInput";
import { NodeConfigPanel } from "./NodeConfigPanel";

const PRIMARY_INPUT: ConfigFieldSchema = {
  key: "primary_input",
  label: "Primary Input",
  field_type: "task_ref",
  required: false,
  options: [],
};

const NODE_TYPES: NodeTypeInfoSchema[] = [
  {
    type: "source.liked_tracks",
    category: "source",
    description: "Fetch liked tracks",
    config_fields: [],
  },
  {
    type: "filter.by_tracks",
    category: "filter",
    description: "Exclude tracks present in another input",
    config_fields: [
      {
        key: "exclusion_source",
        label: "Exclusion Source",
        field_type: "task_ref",
        required: true,
        options: [],
      },
      PRIMARY_INPUT,
    ],
  },
  {
    type: "enricher.play_history",
    category: "enricher",
    description: "Attach play-history metrics",
    config_fields: [
      {
        key: "metrics",
        label: "Metrics",
        field_type: "multi_select",
        required: false,
        default: ["total_plays", "last_played_dates"],
        options: [
          { value: "total_plays", label: "Total Plays" },
          { value: "last_played_dates", label: "Last Played" },
          { value: "period_plays", label: "Period Plays" },
        ],
      },
      PRIMARY_INPUT,
    ],
  },
  {
    type: "sorter.by_metric",
    category: "sorter",
    description: "Sort by a metric",
    config_fields: [
      {
        key: "reverse",
        label: "Highest First",
        field_type: "boolean",
        required: false,
        default: true,
        options: [],
      },
      PRIMARY_INPUT,
    ],
  },
];

function makeNode(
  id: string,
  nodeType: string,
  config: Record<string, unknown> = {},
): Node {
  return {
    id,
    type: nodeType.split(".")[0],
    position: { x: 0, y: 0 },
    data: { taskId: id, nodeType, config },
  };
}

function edge(source: string, target: string) {
  return { id: `e-${source}-${target}`, source, target };
}

function selectedConfig(): Record<string, unknown> {
  const s = useEditorStore.getState();
  const node = s.nodes.find((n) => n.id === s.selectedNodeId);
  return (node?.data.config as Record<string, unknown>) ?? {};
}

describe("NodeConfigPanel", () => {
  beforeEach(() => {
    server.use(
      http.get("*/api/v1/workflows/nodes", () =>
        HttpResponse.json(NODE_TYPES, { status: 200 }),
      ),
    );
  });

  it("renders nothing when no node is selected", () => {
    useEditorStore.setState({ selectedNodeId: null, nodes: [] });
    const { container } = renderWithProviders(<NodeConfigPanel />);
    // Panel is hidden — no visible content
    expect(container.textContent).toBe("");
  });

  it("renders panel with node type badge when node is selected", () => {
    useEditorStore.setState({
      selectedNodeId: "src_1",
      nodes: [
        {
          id: "src_1",
          type: "source",
          position: { x: 0, y: 0 },
          data: {
            taskId: "src_1",
            nodeType: "source.liked_tracks",
            config: {},
          },
        },
      ],
      edges: [],
    });

    renderWithProviders(<NodeConfigPanel />);

    // Should show the close button and node category badge
    expect(screen.getByLabelText("Close panel")).toBeInTheDocument();
    expect(screen.getByText("source")).toBeInTheDocument();
    // Task ID field
    expect(screen.getByLabelText("Task ID")).toBeInTheDocument();
  });

  it("shows no-config message for nodes without schema", () => {
    useEditorStore.setState({
      selectedNodeId: "src_1",
      nodes: [
        {
          id: "src_1",
          type: "source",
          position: { x: 0, y: 0 },
          data: {
            taskId: "src_1",
            nodeType: "source.liked_tracks",
            config: {},
          },
        },
      ],
      edges: [],
    });

    renderWithProviders(<NodeConfigPanel />);
    expect(screen.getByText("No configuration needed")).toBeInTheDocument();
  });

  describe("task_ref fields", () => {
    it("renders a disabled picker with a hint when nothing is connected", async () => {
      useEditorStore.setState({
        selectedNodeId: "filter_1",
        nodes: [makeNode("filter_1", "filter.by_tracks")],
        edges: [],
      });

      renderWithProviders(<NodeConfigPanel />);

      const picker = await screen.findByLabelText(/Exclusion Source/);
      expect(picker).toBeDisabled();
      expect(picker).toHaveTextContent(NO_UPSTREAM_MESSAGE);
    });

    it("offers the upstream tasks and writes the chosen id to config", async () => {
      const user = userEvent.setup();
      useEditorStore.setState({
        selectedNodeId: "filter_1",
        nodes: [
          makeNode("src_1", "source.liked_tracks"),
          makeNode("src_2", "source.liked_tracks"),
          makeNode("filter_1", "filter.by_tracks"),
        ],
        edges: [edge("src_1", "filter_1"), edge("src_2", "filter_1")],
      });

      renderWithProviders(<NodeConfigPanel />);

      const picker = await screen.findByLabelText(/Exclusion Source/);
      expect(picker).toBeEnabled();
      await user.click(picker);
      await user.click(await screen.findByRole("option", { name: /src_2/ }));

      expect(selectedConfig()).toEqual({ exclusion_source: "src_2" });
    });

    it("hides primary_input with a single upstream and shows it with two", async () => {
      useEditorStore.setState({
        selectedNodeId: "filter_1",
        nodes: [
          makeNode("src_1", "source.liked_tracks"),
          makeNode("src_2", "source.liked_tracks"),
          makeNode("filter_1", "filter.by_tracks"),
        ],
        edges: [edge("src_1", "filter_1")],
      });

      renderWithProviders(<NodeConfigPanel />);

      await screen.findByLabelText(/Exclusion Source/);
      expect(screen.queryByText("Primary Input")).not.toBeInTheDocument();

      useEditorStore.setState({
        edges: [edge("src_1", "filter_1"), edge("src_2", "filter_1")],
      });

      await waitFor(() => {
        expect(screen.getByText("Primary Input")).toBeInTheDocument();
      });
    });
  });

  describe("multi_select fields", () => {
    it("pre-checks the declared default without touching config", async () => {
      useEditorStore.setState({
        selectedNodeId: "enrich_1",
        nodes: [makeNode("enrich_1", "enricher.play_history")],
        edges: [],
      });

      renderWithProviders(<NodeConfigPanel />);

      expect(await screen.findByLabelText("Total Plays")).toBeChecked();
      expect(screen.getByLabelText("Last Played")).toBeChecked();
      expect(screen.getByLabelText("Period Plays")).not.toBeChecked();
      expect(selectedConfig()).toEqual({});
    });

    it("writes the whole selection on toggle and drops the key when emptied", async () => {
      const user = userEvent.setup();
      useEditorStore.setState({
        selectedNodeId: "enrich_1",
        nodes: [
          makeNode("enrich_1", "enricher.play_history", {
            metrics: ["period_plays"],
          }),
        ],
        edges: [],
      });

      renderWithProviders(<NodeConfigPanel />);

      await user.click(await screen.findByLabelText("Total Plays"));
      expect(selectedConfig()).toEqual({
        metrics: ["total_plays", "period_plays"],
      });

      await user.click(screen.getByLabelText("Total Plays"));
      await user.click(screen.getByLabelText("Period Plays"));
      expect(selectedConfig()).toEqual({});
    });
  });

  describe("boolean fields", () => {
    it("shows a true default as on and only writes config on toggle", async () => {
      const user = userEvent.setup();
      useEditorStore.setState({
        selectedNodeId: "sort_1",
        nodes: [makeNode("sort_1", "sorter.by_metric")],
        edges: [],
      });

      renderWithProviders(<NodeConfigPanel />);

      const toggle = await screen.findByLabelText(/Highest First/);
      expect(toggle).toHaveAttribute("aria-checked", "true");
      expect(selectedConfig()).toEqual({});

      await user.click(toggle);
      expect(selectedConfig()).toEqual({ reverse: false });
    });

    it.each([
      ["false", "false"],
      ["true", "true"],
    ])(
      "reads the legacy string %s as the switch state",
      async (saved, ariaChecked) => {
        useEditorStore.setState({
          selectedNodeId: "sort_1",
          nodes: [makeNode("sort_1", "sorter.by_metric", { reverse: saved })],
          edges: [],
        });

        renderWithProviders(<NodeConfigPanel />);

        const toggle = await screen.findByLabelText(/Highest First/);
        expect(toggle).toHaveAttribute("aria-checked", ariaChecked);
      },
    );

    it("persists a real boolean when a legacy string value is toggled", async () => {
      const user = userEvent.setup();
      useEditorStore.setState({
        selectedNodeId: "sort_1",
        nodes: [makeNode("sort_1", "sorter.by_metric", { reverse: "false" })],
        edges: [],
      });

      renderWithProviders(<NodeConfigPanel />);

      await user.click(await screen.findByLabelText(/Highest First/));
      expect(selectedConfig()).toEqual({ reverse: true });
    });
  });
});
