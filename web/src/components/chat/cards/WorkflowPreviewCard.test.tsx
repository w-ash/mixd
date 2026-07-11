import { fireEvent, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useChatStore } from "#/stores/chat-store";
import { renderWithProviders } from "#/test/test-utils";

import { WorkflowPreviewCard } from "./WorkflowPreviewCard";
import {
  type GenerateWorkflowResult,
  isGenerateWorkflowResult,
} from "./workflow-preview-types";

vi.mock("#/components/shared/WorkflowGraph", () => ({
  WorkflowGraph: ({ tasks }: { tasks: unknown[] }) => (
    <div data-testid="workflow-graph">{tasks.length} tasks</div>
  ),
}));

const RESULT: GenerateWorkflowResult = {
  status: "valid",
  workflow_def: {
    id: "chill-weekend",
    name: "Chill Weekend",
    description: "Liked tracks you haven't played lately.",
    version: "1.0",
    tasks: [
      { id: "src", type: "source.liked_tracks", config: {}, upstream: [] },
      {
        id: "dest",
        type: "destination.create_playlist",
        config: { name: "Chill Weekend" },
        upstream: ["src"],
      },
    ],
  },
  warnings: [],
  task_count: 2,
};

function resetStore() {
  useChatStore.setState({
    messages: [],
    isStreaming: false,
    confirmationStates: {},
    currentWorkflowDraft: null,
  });
}

beforeEach(resetStore);

describe("WorkflowPreviewCard", () => {
  it("renders the definition as a graph with name and node count", () => {
    renderWithProviders(
      <WorkflowPreviewCard toolCallId="g1" result={RESULT} />,
    );

    expect(screen.getByText("Chill Weekend")).toBeInTheDocument();
    expect(screen.getByText("2 nodes")).toBeInTheDocument();
    expect(screen.getByTestId("workflow-graph")).toHaveTextContent("2 tasks");
  });

  it("Save confirms the sibling proposal and the label reflects create mode", () => {
    const onConfirm = vi.fn();
    renderWithProviders(
      <WorkflowPreviewCard
        toolCallId="g1"
        result={RESULT}
        saveProposal={{ actionId: "a1", mode: "create" }}
        onConfirm={onConfirm}
      />,
    );

    const save = screen.getByRole("button", { name: "Save workflow" });
    fireEvent.click(save);
    expect(onConfirm).toHaveBeenCalledWith("a1");
  });

  it("label flips to Save changes for update proposals", () => {
    renderWithProviders(
      <WorkflowPreviewCard
        toolCallId="g1"
        result={RESULT}
        saveProposal={{ actionId: "a1", mode: "update" }}
        onConfirm={vi.fn()}
      />,
    );

    expect(
      screen.getByRole("button", { name: "Save changes" }),
    ).toBeInTheDocument();
  });

  it("falls back to a synthetic message when no proposal exists", () => {
    const onSendMessage = vi.fn();
    renderWithProviders(
      <WorkflowPreviewCard
        toolCallId="g1"
        result={RESULT}
        onSendMessage={onSendMessage}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Save workflow" }));
    expect(onSendMessage).toHaveBeenCalledWith("Save this workflow.");
  });

  it("Discard cancels the proposal", () => {
    const onCancel = vi.fn();
    renderWithProviders(
      <WorkflowPreviewCard
        toolCallId="g1"
        result={RESULT}
        saveProposal={{ actionId: "a1", mode: "create" }}
        onConfirm={vi.fn()}
        onCancel={onCancel}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Discard" }));
    expect(onCancel).toHaveBeenCalledWith("a1");
  });

  it("collapses when a newer generate call exists in the conversation", () => {
    useChatStore.setState({
      messages: [
        {
          id: "m2",
          role: "assistant",
          content: "",
          toolCalls: [{ id: "g2", name: "generate_workflow_def" }],
        },
      ],
    });

    renderWithProviders(
      <WorkflowPreviewCard toolCallId="g1" result={RESULT} />,
    );

    expect(screen.getByText(/replaced by a newer draft/i)).toBeInTheDocument();
    expect(screen.queryByTestId("workflow-graph")).not.toBeInTheDocument();
  });

  it("shows the saved state and workflows link once confirmed", () => {
    useChatStore.setState({ confirmationStates: { a1: "confirmed" } });

    renderWithProviders(
      <WorkflowPreviewCard
        toolCallId="g1"
        result={RESULT}
        saveProposal={{ actionId: "a1", mode: "create" }}
        onConfirm={vi.fn()}
      />,
    );

    expect(screen.getByText("Saved")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /open in workflows/i }),
    ).toHaveAttribute("href", "/workflows");
    expect(
      screen.queryByRole("button", { name: "Save workflow" }),
    ).not.toBeInTheDocument();
  });

  it("renders validation warnings", () => {
    renderWithProviders(
      <WorkflowPreviewCard
        toolCallId="g1"
        result={{
          ...RESULT,
          warnings: [
            { task_id: "flt", field: "config", message: "needs an enricher" },
          ],
        }}
      />,
    );

    expect(screen.getByText(/needs an enricher/)).toBeInTheDocument();
  });
});

describe("isGenerateWorkflowResult", () => {
  it("accepts the dispatcher payload and rejects near-misses", () => {
    expect(isGenerateWorkflowResult(RESULT)).toBe(true);
    expect(isGenerateWorkflowResult({ status: "valid" })).toBe(false);
    expect(isGenerateWorkflowResult(null)).toBe(false);
    expect(
      isGenerateWorkflowResult({
        status: "valid",
        workflow_def: { name: "x", tasks: [{ id: 1 }] },
      }),
    ).toBe(false);
  });
});
