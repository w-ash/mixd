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

const GENERATE_CALL: ToolCall = {
  id: "g1",
  name: "generate_workflow_def",
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
    isStreaming: false,
    confirmationStates: {},
    currentWorkflowDraft: null,
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

  it("keeps the generic card for ordinary read results", () => {
    renderWithProviders(
      <ToolResultCard
        toolCall={{
          id: "t1",
          name: "list_user_workflows",
          result: { total_count: 2 },
        }}
      />,
    );

    expect(screen.getByText("total count")).toBeInTheDocument();
  });
});
