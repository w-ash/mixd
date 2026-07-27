import { describe, expect, it, vi } from "vitest";

// Mock smart-edge package (CommonJS/ESM incompatibility in jsdom)
vi.mock("@jalez/react-flow-smart-edge", () => ({
  SmartBezierEdge: () => null,
}));

// Mock React Flow hooks
vi.mock("@xyflow/react", () => ({
  useReactFlow: () => ({
    fitView: vi.fn(),
  }),
  ReactFlowProvider: ({ children }: { children: React.ReactNode }) => children,
}));

// Mock layout to avoid async ELK
vi.mock("#/lib/workflow-layout", () => ({
  layoutWorkflow: vi.fn().mockResolvedValue({ nodes: [], edges: [] }),
  buildEdges: vi.fn().mockReturnValue({ flowEdges: [] }),
  generateNodeId: vi.fn().mockReturnValue("node_1"),
  getNodeCategoryName: vi.fn().mockReturnValue("source"),
  createInitialNodes: vi.fn().mockReturnValue({ nodes: [], edges: [] }),
}));

// Mock only the download side of workflow-file (jsdom has no real download);
// keep parseWorkflowFile + loadImportedWorkflowDef real so import truly seeds
// the store.
const { mockDownload } = vi.hoisted(() => ({ mockDownload: vi.fn() }));
vi.mock("#/lib/workflow-file", async (importActual) => {
  const actual = await importActual<typeof import("#/lib/workflow-file")>();
  return { ...actual, downloadWorkflowDef: mockDownload };
});

// Mock the execution hook so we can assert Run wiring without a live run.
const { mockExecute } = vi.hoisted(() => ({ mockExecute: vi.fn() }));
vi.mock("#/hooks/useWorkflowExecution", () => ({
  useWorkflowExecution: () => ({
    isExecuting: false,
    execute: mockExecute,
    operationId: null,
    runId: null,
    nodeStatuses: new Map(),
    runAccepted: false,
    subProgress: null,
    error: null,
  }),
}));

import { fireEvent } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { getGetWorkflowApiV1WorkflowsWorkflowIdGetQueryKey } from "#/api/generated/workflows/workflows";
import { toasts } from "#/lib/toasts";
import { useEditorStore } from "#/stores/editor-store";
import { server } from "#/test/setup";
import {
  createTestQueryClient,
  renderWithProviders,
  screen,
  waitFor,
} from "#/test/test-utils";

import { EditorToolbar } from "./EditorToolbar";

/** A node shaped enough for hasNodes / toWorkflowDef. */
function seedNode(id = "n1") {
  return {
    id,
    type: "source",
    position: { x: 0, y: 0 },
    data: { taskId: id, nodeType: "source.liked_tracks", config: {} },
  };
}

function fileFromDef(def: unknown): File {
  return new File([JSON.stringify(def)], "wf.json", {
    type: "application/json",
  });
}

describe("EditorToolbar", () => {
  it("renders core toolbar buttons", () => {
    renderWithProviders(<EditorToolbar />);

    expect(screen.getByLabelText("Back")).toBeInTheDocument();
    expect(screen.getByLabelText("Workflow name")).toBeInTheDocument();
    expect(screen.getByText("Save")).toBeInTheDocument();
    expect(screen.getByText("Preview")).toBeInTheDocument();
    expect(screen.getByLabelText("Undo")).toBeInTheDocument();
    expect(screen.getByLabelText("Redo")).toBeInTheDocument();
    expect(screen.getByLabelText("Auto layout")).toBeInTheDocument();
    expect(screen.getByLabelText("Zoom to fit")).toBeInTheDocument();
    expect(screen.getByLabelText("Delete selected")).toBeInTheDocument();
  });

  it("shows workflow name input with default value", () => {
    renderWithProviders(<EditorToolbar />);
    const input = screen.getByLabelText("Workflow name") as HTMLInputElement;
    expect(input.value).toBe("Untitled Workflow");
  });

  it("disables undo/redo when history is empty", () => {
    renderWithProviders(<EditorToolbar />);
    expect(screen.getByLabelText("Undo")).toBeDisabled();
    expect(screen.getByLabelText("Redo")).toBeDisabled();
  });

  it("shows Run and History buttons for saved workflows", () => {
    // Set a workflow ID to simulate saved state
    useEditorStore.setState({
      workflowId: "019d0000-0000-7000-8000-000000000042",
    });
    renderWithProviders(<EditorToolbar />);

    expect(screen.getByText("Run")).toBeInTheDocument();
    expect(screen.getByText("History")).toBeInTheDocument();

    // Clean up
    useEditorStore.setState({ workflowId: null });
  });

  it("Run button triggers workflow execution (no dead window event)", () => {
    mockExecute.mockClear();
    useEditorStore.setState({
      workflowId: "019d0000-0000-7000-8000-000000000042",
    });
    renderWithProviders(<EditorToolbar />);

    fireEvent.click(screen.getByText("Run"));
    expect(mockExecute).toHaveBeenCalledTimes(1);

    useEditorStore.setState({ workflowId: null });
  });

  it("disables Run while the canvas has unsaved edits (isDirty)", () => {
    // Run executes the SAVED definition; with pending edits it must not
    // silently run the stale saved version behind the user's back.
    mockExecute.mockClear();
    useEditorStore.setState({
      workflowId: "019d0000-0000-7000-8000-000000000042",
      isDirty: true,
    });
    renderWithProviders(<EditorToolbar />);

    expect(screen.getByText("Run").closest("button")).toBeDisabled();
    fireEvent.click(screen.getByText("Run"));
    expect(mockExecute).not.toHaveBeenCalled();

    useEditorStore.setState({ workflowId: null, isDirty: false });
  });

  it("hides Run and History buttons for new workflows", () => {
    useEditorStore.setState({ workflowId: null });
    renderWithProviders(<EditorToolbar />);

    expect(screen.queryByText("Run")).not.toBeInTheDocument();
    expect(screen.queryByText("History")).not.toBeInTheDocument();
  });

  it("disables Export on an empty canvas, enables it with nodes", () => {
    useEditorStore.setState({ nodes: [] });
    const { rerender } = renderWithProviders(<EditorToolbar />);
    expect(screen.getByLabelText("Export workflow")).toBeDisabled();

    useEditorStore.setState({ nodes: [seedNode()] });
    rerender(<EditorToolbar />);
    expect(screen.getByLabelText("Export workflow")).not.toBeDisabled();
  });

  it("Export downloads the current store definition", () => {
    mockDownload.mockClear();
    useEditorStore.setState({
      nodes: [seedNode()],
      workflowName: "My Mix",
      workflowId: null,
    });
    renderWithProviders(<EditorToolbar />);

    fireEvent.click(screen.getByLabelText("Export workflow"));

    expect(mockDownload).toHaveBeenCalledOnce();
    expect(mockDownload.mock.calls[0][0]).toMatchObject({ name: "My Mix" });
  });

  it("Import loads a valid file as an unsaved draft", async () => {
    useEditorStore.setState({
      workflowId: "existing",
      workflowName: "Old",
      isDirty: false,
    });
    renderWithProviders(<EditorToolbar />);

    const input = screen.getByLabelText(
      "Import workflow file",
    ) as HTMLInputElement;
    fireEvent.change(input, {
      target: { files: [fileFromDef({ name: "Imported Flow", tasks: [] })] },
    });

    await waitFor(() => {
      expect(useEditorStore.getState().workflowName).toBe("Imported Flow");
    });
    expect(useEditorStore.getState().workflowId).toBeNull();
    expect(useEditorStore.getState().isDirty).toBe(true);
  });

  describe("save writes the detail cache", () => {
    const SAVED_ID = "019d0000-0000-7000-8000-000000000042";

    /** A detail payload distinguishable from whatever the GET would return. */
    function savedWorkflow(overrides: Record<string, unknown> = {}) {
      return {
        id: SAVED_ID,
        name: "Renamed Flow",
        description: null,
        definition_version: 2,
        task_count: 4,
        node_types: ["source.liked_tracks"],
        created_at: "2026-05-30T00:00:00Z",
        updated_at: "2026-06-01T00:00:00Z",
        last_run: null,
        successful_run_count: 7,
        definition: { id: "wf", name: "Renamed Flow", tasks: [] },
        ...overrides,
      };
    }

    it("seeds the detail query from the PATCH response", async () => {
      // The bug this guards: without the cache write the detail page renders
      // the pre-edit workflow for the full 2-minute staleTime window.
      server.use(
        http.patch(`*/api/v1/workflows/${SAVED_ID}`, () =>
          HttpResponse.json(savedWorkflow()),
        ),
      );

      const queryClient = createTestQueryClient();
      useEditorStore.setState({
        workflowId: SAVED_ID,
        nodes: [seedNode()],
        isDirty: true,
      });
      renderWithProviders(<EditorToolbar />, { queryClient });

      fireEvent.click(screen.getByText("Save"));

      await waitFor(() => {
        const cached = queryClient.getQueryData(
          getGetWorkflowApiV1WorkflowsWorkflowIdGetQueryKey(SAVED_ID),
        );
        expect(cached).toMatchObject({
          status: 200,
          data: { definition_version: 2, task_count: 4, name: "Renamed Flow" },
        });
      });
    });

    it("normalises the 201 create response to a 200 cache envelope", async () => {
      // Read sites branch on `status === 200`; writing the raw 201 would make
      // the seeded entry invisible to every consumer.
      server.use(
        http.post("*/api/v1/workflows", () =>
          HttpResponse.json(savedWorkflow(), { status: 201 }),
        ),
      );

      const queryClient = createTestQueryClient();
      useEditorStore.setState({ workflowId: null, nodes: [seedNode()] });
      renderWithProviders(<EditorToolbar />, { queryClient });

      fireEvent.click(screen.getByText("Save"));

      await waitFor(() => {
        const cached = queryClient.getQueryData(
          getGetWorkflowApiV1WorkflowsWorkflowIdGetQueryKey(SAVED_ID),
        );
        expect(cached).toMatchObject({ status: 200 });
      });
    });

    it("invalidates the workflow list after a save", async () => {
      server.use(
        http.patch(`*/api/v1/workflows/${SAVED_ID}`, () =>
          HttpResponse.json(savedWorkflow()),
        ),
      );

      const queryClient = createTestQueryClient();
      const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
      useEditorStore.setState({
        workflowId: SAVED_ID,
        nodes: [seedNode()],
        isDirty: true,
      });
      renderWithProviders(<EditorToolbar />, { queryClient });

      fireEvent.click(screen.getByText("Save"));

      await waitFor(() => {
        const urls = invalidateSpy.mock.calls.flatMap((call) => {
          const key = (call[0] as { queryKey?: unknown[] } | undefined)
            ?.queryKey;
          return typeof key?.[0] === "string" ? [key[0]] : [];
        });
        expect(urls).toContain("/api/v1/workflows");
        expect(urls).toContain(`/api/v1/workflows/${SAVED_ID}/versions`);
      });
    });
  });

  it("Import of an invalid file surfaces an error toast", async () => {
    const errorSpy = vi.spyOn(toasts, "error").mockImplementation(() => {});
    useEditorStore.setState({ workflowName: "Untouched" });
    renderWithProviders(<EditorToolbar />);

    const input = screen.getByLabelText(
      "Import workflow file",
    ) as HTMLInputElement;
    fireEvent.change(input, {
      target: { files: [new File(["not json {"], "bad.json")] },
    });

    await waitFor(() => {
      expect(errorSpy).toHaveBeenCalledWith(
        "Couldn't import workflow",
        expect.any(Error),
      );
    });
    expect(useEditorStore.getState().workflowName).toBe("Untouched");
    errorSpy.mockRestore();
  });
});
