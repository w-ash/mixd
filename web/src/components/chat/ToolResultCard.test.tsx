import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { type ToolCall, useChatStore } from "#/stores/chat-store";
import { renderWithProviders } from "#/test/test-utils";

import { ToolResultCard } from "./ToolResultCard";

vi.mock("#/components/shared/WorkflowGraph", () => ({
  WorkflowGraph: ({ tasks }: { tasks: unknown[] }) => (
    <div data-testid="workflow-graph">{tasks.length} tasks</div>
  ),
}));

// The operation-progress card opens an SSE stream on mount — stub the hook so
// the dispatch test stays a pure render check.
vi.mock("#/hooks/useOperationProgress", () => ({
  useOperationProgress: () => ({
    progress: {
      status: "running",
      current: 1,
      total: 10,
      message: "Importing plays…",
      description: "Import Last.fm history",
      completionPercentage: 10,
      itemsPerSecond: null,
      etaSeconds: null,
      counts: null,
      subOperation: null,
      subOperationHistory: {},
    },
    isActive: true,
    isConnected: true,
    error: null,
  }),
}));

const GENERATE_CALL: ToolCall = {
  id: "g1",
  name: "generate_workflow_def",
  kind: "read",
  result: {
    status: "valid",
    workflow_def: {
      id: "wf",
      name: "Chill Weekend",
      description: "",
      version: "1.0",
      tasks: [
        { id: "src", type: "source.liked_tracks", config: {}, upstream: [] },
      ],
    },
    warnings: [],
    task_count: 1,
  },
};

const SAVE_CALL: ToolCall = {
  id: "s1",
  name: "save_workflow",
  kind: "write",
  result: {
    status: "pending_confirmation",
    action_id: "a1",
    description: "Create workflow 'Chill Weekend' with 1 task",
    details: {
      mode: "create",
      name: "Chill Weekend",
      task_count: 1,
      definition: { id: "wf", name: "Chill Weekend", tasks: [] },
    },
  },
};

beforeEach(() => {
  useChatStore.setState({
    messages: [],
    confirmationStates: {},
  });
});

describe("ToolResultCard dispatch", () => {
  it("routes generate_workflow_def to the preview card", async () => {
    renderWithProviders(
      <ToolResultCard
        toolCall={GENERATE_CALL}
        siblingToolCalls={[GENERATE_CALL, SAVE_CALL]}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    // Lazy-loaded under Suspense — wait for the chunk to resolve.
    expect(await screen.findByTestId("workflow-graph")).toBeInTheDocument();
    // The sibling save proposal supplies the Save affordance on the card.
    expect(
      screen.getByRole("button", { name: "Save workflow" }),
    ).toBeInTheDocument();
  });

  it("suppresses the save confirmation when a sibling preview exists", () => {
    const { container } = renderWithProviders(
      <ToolResultCard
        toolCall={SAVE_CALL}
        siblingToolCalls={[GENERATE_CALL, SAVE_CALL]}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    expect(container).toBeEmptyDOMElement();
  });

  it("renders the save confirmation without the raw definition when alone", () => {
    renderWithProviders(
      <ToolResultCard
        toolCall={SAVE_CALL}
        siblingToolCalls={[SAVE_CALL]}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    expect(
      screen.getByText("Create workflow 'Chill Weekend' with 1 task"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Confirm" })).toBeInTheDocument();
    // The definition JSON wall is stripped from the generic details view.
    expect(screen.queryByText(/definition/)).not.toBeInTheDocument();
  });

  it("routes an operation_started result to the progress card", () => {
    renderWithProviders(
      <ToolResultCard
        toolCall={{
          id: "op1",
          name: "import_lastfm_history",
          kind: "write",
          result: {
            status: "operation_started",
            operation_id: "op-123",
            run_id: "run-456",
            description: "Import Last.fm history",
          },
        }}
      />,
    );

    expect(screen.getByText("Import Last.fm history")).toBeInTheDocument();
    expect(screen.getByText("Importing plays…")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /open run/i })).toHaveAttribute(
      "href",
      "/settings/imports?run=run-456",
    );
  });

  it("keeps the generic card for ordinary read results", () => {
    renderWithProviders(
      <ToolResultCard
        toolCall={{
          id: "t1",
          name: "list_user_workflows",
          kind: "read",
          result: { total_count: 2 },
        }}
      />,
    );

    expect(screen.getByText("total count")).toBeInTheDocument();
  });
});
