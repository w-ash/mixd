import { describe, expect, it } from "vitest";

import { renderWithProviders, screen } from "#/test/test-utils";
import { LastRunCard } from "./LastRunCard";

describe("LastRunCard", () => {
  it("shows 'No runs yet' when run is null", () => {
    renderWithProviders(
      <LastRunCard
        run={null}
        currentDefinitionVersion={1}
        workflowId="019d0000-0000-7000-8000-000000000001"
      />,
    );

    expect(screen.getByText("No runs yet")).toBeInTheDocument();
  });

  it("renders run status and track count", () => {
    renderWithProviders(
      <LastRunCard
        run={{
          id: "019d0000-0000-7000-8000-000000000005",
          status: "completed",
          definition_version: 2,
          completed_at: "2026-02-15T11:00:00Z",
          output_track_count: 42,
        }}
        currentDefinitionVersion={2}
        workflowId="019d0000-0000-7000-8000-000000000001"
      />,
    );

    expect(screen.getByText("Completed")).toBeInTheDocument();
    expect(screen.getByText("42 tracks")).toBeInTheDocument();
  });

  it("shows version mismatch warning when definition is newer", () => {
    renderWithProviders(
      <LastRunCard
        run={{
          id: "019d0000-0000-7000-8000-000000000005",
          status: "completed",
          definition_version: 1,
          completed_at: "2026-02-15T11:00:00Z",
          output_track_count: 20,
        }}
        currentDefinitionVersion={3}
        workflowId="019d0000-0000-7000-8000-000000000001"
      />,
    );

    expect(
      screen.getByText("Definition changed since last run"),
    ).toBeInTheDocument();
  });

  it("does not show version mismatch when versions match", () => {
    renderWithProviders(
      <LastRunCard
        run={{
          id: "019d0000-0000-7000-8000-000000000005",
          status: "completed",
          definition_version: 3,
          completed_at: "2026-02-15T11:00:00Z",
          output_track_count: 20,
        }}
        currentDefinitionVersion={3}
        workflowId="019d0000-0000-7000-8000-000000000001"
      />,
    );

    expect(
      screen.queryByText("Definition changed since last run"),
    ).not.toBeInTheDocument();
  });

  it("links to run detail page", () => {
    renderWithProviders(
      <LastRunCard
        run={{
          id: "019d0000-0000-7000-8000-000000000005",
          status: "completed",
          definition_version: 2,
          completed_at: "2026-02-15T11:00:00Z",
          output_track_count: 20,
        }}
        currentDefinitionVersion={2}
        workflowId="019d0000-0000-7000-8000-000000000007"
      />,
    );

    const link = screen.getByText("Details").closest("a");
    expect(link).toHaveAttribute(
      "href",
      "/workflows/019d0000-0000-7000-8000-000000000007/runs/019d0000-0000-7000-8000-000000000005",
    );
  });
});
