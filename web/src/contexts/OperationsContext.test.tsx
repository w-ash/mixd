import { HttpResponse, http } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { OperationRunSummarySchema } from "#/api/generated/model";
import {
  __resetRunToastLedger,
  claimRunToast,
} from "#/lib/operation-toast-ledger";
import { server } from "#/test/setup";
import { renderWithProviders, waitFor } from "#/test/test-utils";

import { OperationsProvider } from "./OperationsContext";

// Drive the polled "active operations" list deterministically.
let mockActiveOps: OperationRunSummarySchema[] = [];
vi.mock("#/hooks/useActiveOperations", () => ({
  useActiveOperations: () => ({ data: mockActiveOps }),
  useActiveOperation: () => ({ data: null }),
}));

const mockToastMessage = vi.fn();
vi.mock("#/lib/toasts", async () => {
  const actual =
    await vi.importActual<typeof import("#/lib/toasts")>("#/lib/toasts");
  return {
    ...actual,
    toasts: {
      ...actual.toasts,
      message: (...a: unknown[]) => mockToastMessage(...a),
    },
  };
});

function runningRow(
  id: string,
  operationId: string,
): OperationRunSummarySchema {
  return {
    id,
    operation_id: operationId,
    operation_type: "import_connector_playlists",
    started_at: "2026-06-23T00:00:00Z",
    ended_at: null,
    status: "running",
    counts: {},
    issue_count: 0,
  };
}

/** Stub the per-run detail endpoint with a given terminal status. */
function stubDetail(runId: string, status: string, issues: object[] = []) {
  server.use(
    http.get(`*/api/v1/operation-runs/${runId}`, () =>
      HttpResponse.json({
        id: runId,
        operation_id: `op-${runId}`,
        operation_type: "import_connector_playlists",
        started_at: "2026-06-23T00:00:00Z",
        ended_at: "2026-06-23T00:01:00Z",
        status,
        counts: {},
        issues,
      }),
    ),
  );
}

beforeEach(() => {
  mockActiveOps = [];
  mockToastMessage.mockReset();
  __resetRunToastLedger();
});
afterEach(() => {
  vi.restoreAllMocks();
});

describe("OperationsProvider failure surfacing", () => {
  it("does not retro-toast a failure that predates mount", async () => {
    // No running ops at mount: a pre-existing terminal failure is simply absent
    // from the running set, so it can never be diffed into a toast.
    mockActiveOps = [];
    renderWithProviders(<OperationsProvider>{null}</OperationsProvider>);

    await new Promise((r) => setTimeout(r, 20));
    expect(mockToastMessage).not.toHaveBeenCalled();
  });

  it("toasts when an observed running op transitions to error", async () => {
    stubDetail("run-1", "error", [{ connector_playlist_identifier: "pl1" }]);
    mockActiveOps = [runningRow("run-1", "op-1")];
    const { rerender } = renderWithProviders(
      <OperationsProvider>{null}</OperationsProvider>,
    );

    // run-1 leaves the running set → terminal status resolved as error.
    mockActiveOps = [];
    rerender(<OperationsProvider>{null}</OperationsProvider>);

    await waitFor(() => {
      expect(mockToastMessage).toHaveBeenCalledWith(
        "Import failed",
        expect.objectContaining({ action: expect.anything() }),
      );
    });
  });

  it("does not toast when an op completes cleanly", async () => {
    stubDetail("run-2", "complete");
    mockActiveOps = [runningRow("run-2", "op-2")];
    const { rerender } = renderWithProviders(
      <OperationsProvider>{null}</OperationsProvider>,
    );

    mockActiveOps = [];
    rerender(<OperationsProvider>{null}</OperationsProvider>);

    await new Promise((r) => setTimeout(r, 20));
    expect(mockToastMessage).not.toHaveBeenCalled();
  });

  it("skips a run already claimed by a foreground card", async () => {
    stubDetail("run-3", "error", [{ connector_playlist_identifier: "pl1" }]);
    // A foreground card claimed this run's toast first.
    claimRunToast("run-3");

    mockActiveOps = [runningRow("run-3", "op-3")];
    const { rerender } = renderWithProviders(
      <OperationsProvider>{null}</OperationsProvider>,
    );
    mockActiveOps = [];
    rerender(<OperationsProvider>{null}</OperationsProvider>);

    await new Promise((r) => setTimeout(r, 20));
    expect(mockToastMessage).not.toHaveBeenCalled();
  });
});
