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
    operation_id: null,
    operation_type: "import_lastfm_history",
    started_at: "2026-04-26T10:00:00Z",
    ended_at: "2026-04-26T10:01:00Z",
    status: "complete",
    counts: { tracks: 100 },
    issue_count: 0,
    retryable: false,
    initiated_by: "manual",
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

  it("distinguishes a partial run from a failed one in the list", async () => {
    setupListMock([
      makeSummary({
        id: "00000000-0000-0000-0000-000000000001",
        operation_type: "import_spotify_history",
        status: "partial",
        counts: { track_plays: 98, errors: 2 },
        issue_count: 3,
      }),
      makeSummary({
        id: "00000000-0000-0000-0000-000000000002",
        operation_type: "apply_assignments_bulk",
        status: "error",
      }),
    ]);
    renderWithProviders(<ImportHistoryPage />);

    expect(
      await screen.findByText("Completed with issues"),
    ).toBeInTheDocument();
    expect(screen.getByText("Error")).toBeInTheDocument();
    expect(screen.getByText("3 issues")).toBeInTheDocument();
  });

  it("lists the exact unresolved tracks as legible rows, not JSON blobs", async () => {
    // The point of the feature: "which tracks couldn't be imported?" is
    // answerable from the row, not only from the server logs — and answerable at
    // a glance, which a stringified payload per track is not.
    const targetId = "00000000-0000-0000-0000-000000000077";
    setupListMock([
      makeSummary({ id: targetId, status: "partial", issue_count: 3 }),
    ]);
    setupDetailMock({
      id: targetId,
      operation_id: null,
      operation_type: "import_spotify_history",
      started_at: "2026-04-26T10:00:00Z",
      ended_at: "2026-04-26T10:01:00Z",
      status: "partial",
      counts: { track_plays: 98, errors: 2 },
      issues: [
        { message: "2 of 100 plays failed import" },
        {
          track: "Aphex Twin - Xtal",
          spotify_id: "abc",
          reason: "track_resolution_failed",
        },
        {
          track: "Boards of Canada - Roygbiv",
          spotify_id: "def",
          reason: "track_resolution_failed",
        },
      ],
      retryable: false,
      initiated_by: "manual",
    });

    const { container } = renderWithProviders(<ImportHistoryPage />, {
      routerProps: { initialEntries: [`/settings/imports?run=${targetId}`] },
    });

    await waitFor(() => {
      expect(screen.getByText("Aphex Twin - Xtal")).toBeInTheDocument();
    });
    expect(screen.getByText("Boards of Canada - Roygbiv")).toBeInTheDocument();
    // The reason stays visible — it's what distinguishes "no match found" from
    // "the API refused the call".
    expect(screen.getAllByText("track_resolution_failed")).toHaveLength(2);
    // The headline message is prose, not a one-key object.
    expect(
      screen.getByText("2 of 100 plays failed import"),
    ).toBeInTheDocument();
    // Legibility, not just presence: no raw JSON punctuation for known shapes.
    expect(container.textContent).not.toContain('"track":');
    expect(container.textContent).not.toContain('"message":');
    expect(container.textContent).not.toContain('"spotify_id":');
  });

  it("shows the Assistant badge only for an assistant-initiated run", async () => {
    setupListMock([
      makeSummary({
        id: "00000000-0000-0000-0000-000000000001",
        operation_type: "import_spotify_likes",
        initiated_by: "assistant",
      }),
      makeSummary({
        id: "00000000-0000-0000-0000-000000000002",
        operation_type: "apply_assignments_bulk",
        initiated_by: "manual",
      }),
    ]);
    renderWithProviders(<ImportHistoryPage />);

    // Exactly one Assistant badge — the manual run does not get one.
    await screen.findByText("Spotify likes import");
    expect(screen.getAllByText("Assistant")).toHaveLength(1);
  });

  it("auto-expands ?run=<id>, and falls back to JSON for an unknown issue shape", async () => {
    const targetId = "00000000-0000-0000-0000-000000000042";
    setupListMock([makeSummary({ id: targetId, issue_count: 1 })]);
    setupDetailMock({
      id: targetId,
      operation_id: null,
      operation_type: "import_lastfm_history",
      started_at: "2026-04-26T10:00:00Z",
      ended_at: "2026-04-26T10:01:00Z",
      status: "complete",
      counts: { tracks: 100 },
      issues: [{ track_id: "abc", reason: "no_match" }],
      retryable: false,
      initiated_by: "manual",
    });

    const { container } = renderWithProviders(<ImportHistoryPage />, {
      routerProps: { initialEntries: [`/settings/imports?run=${targetId}`] },
    });

    // Expanded row fires the detail fetch and renders the issue payload. This
    // one is `{track_id, reason}` — not a shape the list knows — so the raw JSON
    // survives rather than the payload vanishing.
    await waitFor(() => {
      expect(screen.getByText(/no_match/)).toBeInTheDocument();
    });
    expect(container.textContent).toContain('"track_id"');
  });
});
