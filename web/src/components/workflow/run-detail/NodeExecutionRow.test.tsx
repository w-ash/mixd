import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { WorkflowRunNodeSchema } from "#/api/generated/model";
import { NodeExecutionRow } from "./NodeExecutionRow";

function makeNode(
  overrides: Partial<WorkflowRunNodeSchema> = {},
): WorkflowRunNodeSchema {
  return {
    id: "2",
    node_id: "filter",
    node_type: "filter.play_count",
    status: "completed",
    started_at: "2026-02-15T10:00:31Z",
    completed_at: "2026-02-15T10:01:00Z",
    duration_ms: 29000,
    input_track_count: 100,
    output_track_count: 15,
    error_message: null,
    execution_order: 2,
    node_details: null,
    ...overrides,
  };
}

describe("NodeExecutionRow", () => {
  it("renders order, node id, and track counts", () => {
    render(<NodeExecutionRow node={makeNode()} />);

    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.getByText("filter")).toBeInTheDocument();
    expect(screen.getByText(/100\s*→\s*15/)).toBeInTheDocument();
  });

  it("omits the counts cell when either count is null", () => {
    render(
      <NodeExecutionRow
        node={makeNode({ input_track_count: null, node_id: "source" })}
      />,
    );

    expect(screen.queryByText(/→/)).not.toBeInTheDocument();
  });

  it("is not interactive without playlist changes", () => {
    render(<NodeExecutionRow node={makeNode()} />);

    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("expands playlist changes on click when details exist", () => {
    render(
      <NodeExecutionRow
        node={makeNode({
          node_id: "update",
          node_type: "destination.update_playlist",
          node_details: {
            playlist_changes: {
              tracks_added: [
                { track_id: 1, title: "Midnight City", artists: "M83" },
              ],
              tracks_removed: [],
              tracks_moved: 0,
              playlist_id: "test-playlist",
              connector: "spotify",
            },
          },
        })}
      />,
    );

    expect(screen.queryByText("Midnight City")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button"));
    expect(screen.getByText("Midnight City")).toBeInTheDocument();
  });
});
