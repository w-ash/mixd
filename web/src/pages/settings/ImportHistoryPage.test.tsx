import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import type {
  OperationRunDetailSchema,
  OperationRunListResponse,
  OperationRunSummarySchema,
} from "#/api/generated/model";
import { server } from "#/test/setup";
import { renderWithProviders, screen, waitFor } from "#/test/test-utils";

import { ImportHistoryPage } from "./ImportHistoryPage";

function makeSummary(
  overrides: Partial<OperationRunSummarySchema> = {},
): OperationRunSummarySchema {
  return {
    id: "00000000-0000-0000-0000-000000000001",
    operation_type: "import_lastfm_history",
    started_at: "2026-04-26T10:00:00Z",
    ended_at: "2026-04-26T10:01:00Z",
    status: "complete",
    counts: { tracks: 100 },
    issue_count: 0,
    ...overrides,
  };
}

function setupListMock(rows: OperationRunSummarySchema[]) {
  const response: OperationRunListResponse = {
    data: rows,
    limit: 20,
    next_cursor: null,
  };
  server.use(
    http.get("*/api/v1/operation-runs", () => HttpResponse.json(response)),
  );
}

function setupDetailMock(detail: OperationRunDetailSchema) {
  server.use(
    http.get("*/api/v1/operation-runs/:id", () => HttpResponse.json(detail)),
  );
}

describe("ImportHistoryPage", () => {
  it("renders the page header", async () => {
    setupListMock([]);
    renderWithProviders(<ImportHistoryPage />);

    expect(
      await screen.findByRole("heading", { name: "Import History" }),
    ).toBeInTheDocument();
  });

  it("shows the empty state when there are no runs", async () => {
    setupListMock([]);
    renderWithProviders(<ImportHistoryPage />);

    expect(await screen.findByText(/No imports yet/i)).toBeInTheDocument();
  });

  it("renders each run with the operation label and status", async () => {
    setupListMock([
      makeSummary({
        id: "00000000-0000-0000-0000-000000000001",
        operation_type: "import_spotify_likes",
        status: "complete",
      }),
      makeSummary({
        id: "00000000-0000-0000-0000-000000000002",
        operation_type: "apply_assignments_bulk",
        status: "error",
      }),
    ]);
    renderWithProviders(<ImportHistoryPage />);

    expect(await screen.findByText("Spotify likes import")).toBeInTheDocument();
    expect(screen.getByText("Apply all assignments")).toBeInTheDocument();
    expect(screen.getByText("Complete")).toBeInTheDocument();
    expect(screen.getByText("Error")).toBeInTheDocument();
  });

  it("auto-expands the row matching ?run=<id> on mount and fetches detail", async () => {
    const targetId = "00000000-0000-0000-0000-000000000042";
    setupListMock([makeSummary({ id: targetId, issue_count: 1 })]);
    setupDetailMock({
      id: targetId,
      operation_type: "import_lastfm_history",
      started_at: "2026-04-26T10:00:00Z",
      ended_at: "2026-04-26T10:01:00Z",
      status: "complete",
      counts: { tracks: 100 },
      issues: [{ track_id: "abc", reason: "no_match" }],
    });

    renderWithProviders(<ImportHistoryPage />, {
      routerProps: { initialEntries: [`/settings/imports?run=${targetId}`] },
    });

    // Expanded row fires the detail fetch and renders the issue payload.
    await waitFor(() => {
      expect(screen.getByText(/no_match/)).toBeInTheDocument();
    });
  });
});
