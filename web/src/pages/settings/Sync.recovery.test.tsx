/**
 * End-to-end recovery for a run whose SSE stream died mid-flight.
 *
 * Sibling of Sync.test.tsx, which stubs `useOperationProgress` to drive the
 * toast directly. This file deliberately runs the REAL hook against a mocked
 * transport so the whole incident path is exercised: stream drops → durable
 * `operation_runs` row is polled → the card renders the same terminal UI and
 * fires the same one-per-run toast the live stream would have.
 */

import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { OperationRunSummarySchema } from "#/api/generated/model";
import {
  __resetRunToastLedger,
  claimRunToast,
} from "#/lib/operation-toast-ledger";
import { server } from "#/test/setup";
import {
  renderWithProviders,
  screen,
  userEvent,
  waitFor,
  within,
} from "#/test/test-utils";

import { Sync } from "./Sync";

vi.mock("#/api/sse-client", () => ({ connectToSSE: vi.fn() }));

import { connectToSSE } from "#/api/sse-client";

const mockRunCompleted = vi.fn();
vi.mock("#/lib/toasts", async () => {
  const actual =
    await vi.importActual<typeof import("#/lib/toasts")>("#/lib/toasts");
  return {
    ...actual,
    toasts: {
      ...actual.toasts,
      runCompleted: (...a: unknown[]) => mockRunCompleted(...a),
    },
  };
});

beforeEach(() => {
  mockRunCompleted.mockReset();
  vi.mocked(connectToSSE).mockReset();
  __resetRunToastLedger();
});

/** The prod shape: some progress lands, then the socket dies with the machine. */
function mockStreamDiesMidRun() {
  vi.mocked(connectToSSE).mockImplementation(async () =>
    (async function* () {
      yield {
        event: "started",
        data: JSON.stringify({
          total: 100,
          description: "Importing scrobbles",
        }),
        id: "evt_1",
      };
      yield {
        event: "progress",
        data: JSON.stringify({ current: 40, total: 100, message: "40/100" }),
        id: "evt_2",
      };
    })(),
  );
}

function mockRunRow(overrides: Partial<OperationRunSummarySchema> = {}) {
  const row: OperationRunSummarySchema = {
    id: "run-1",
    operation_id: "op-1",
    operation_type: "import_lastfm_history",
    started_at: "2026-01-01T10:00:00Z",
    ended_at: "2026-01-01T10:05:00Z",
    status: "complete",
    counts: { track_plays: 98 },
    issue_count: 0,
    retryable: false,
    initiated_by: "user",
    ...overrides,
  };
  server.use(
    http.get("*/api/v1/imports/checkpoints", () => HttpResponse.json([])),
    http.post("*/api/v1/imports/lastfm/history", () =>
      HttpResponse.json({ operation_id: "op-1", run_id: "run-1" }),
    ),
    http.get("*/api/v1/operation-runs", () =>
      HttpResponse.json({ data: [row], limit: 20, next_cursor: null }),
    ),
  );
}

async function startImport() {
  renderWithProviders(<Sync />);
  const card = screen
    .getByText("Scrobble History")
    .closest("div.rounded-xl") as HTMLElement;
  await userEvent.click(within(card).getByRole("button", { name: "Import" }));
  return card;
}

describe("Sync page — SSE stream lost mid-run", () => {
  it("announces a partial run from the durable row, not a connection error", async () => {
    mockStreamDiesMidRun();
    mockRunRow({
      status: "partial",
      counts: { track_plays: 98 },
      issue_count: 2,
    });

    const card = await startImport();

    await waitFor(() => {
      expect(mockRunCompleted).toHaveBeenCalledWith(
        expect.objectContaining({
          operationType: "import_lastfm_history",
          issueCount: 2,
          failed: false,
          runId: "run-1",
        }),
      );
    });
    expect(within(card).getByText("Completed with issues")).toBeInTheDocument();
    expect(screen.queryByText(/Connection failed/)).not.toBeInTheDocument();
    // Exactly one announcement, and the ledger is claimed so the global
    // operations watcher backs off instead of toasting the same run again.
    expect(mockRunCompleted).toHaveBeenCalledTimes(1);
    expect(claimRunToast("run-1")).toBe(false);
  });

  it("announces a clean completion recovered from the row", async () => {
    mockStreamDiesMidRun();
    mockRunRow({ status: "complete", counts: { track_plays: 100 } });

    const card = await startImport();

    await waitFor(() => {
      expect(mockRunCompleted).toHaveBeenCalledWith(
        expect.objectContaining({
          issueCount: 0,
          failed: false,
          runId: "run-1",
        }),
      );
    });
    expect(within(card).getByText("Complete")).toBeInTheDocument();
  });

  it("announces a failed run recovered from the row", async () => {
    mockStreamDiesMidRun();
    mockRunRow({ status: "error", issue_count: 5 });

    const card = await startImport();

    await waitFor(() => {
      expect(mockRunCompleted).toHaveBeenCalledWith(
        expect.objectContaining({
          issueCount: 5,
          failed: true,
          runId: "run-1",
        }),
      );
    });
    expect(within(card).getByText("Operation failed")).toBeInTheDocument();
  });

  it("stays quiet for a cancelled run, matching the global watcher", async () => {
    mockStreamDiesMidRun();
    mockRunRow({ status: "cancelled" });

    const card = await startImport();

    await waitFor(() => {
      expect(within(card).getByText("Cancelled")).toBeInTheDocument();
    });
    expect(mockRunCompleted).not.toHaveBeenCalled();
  });
});
