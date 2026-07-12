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
}));

// Auth gate is bypassed in tests (truthy session → polling on); the build-time
// `authEnabled` is false in the test env anyway, so this is belt-and-braces.
vi.mock("@neondatabase/auth/react/ui", () => ({
  useAuthenticate: () => ({ data: { user: { id: "u" } } }),
}));

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
    retryable: false,
    initiated_by: "manual",
  };
}

/** Stub the per-run detail endpoint with a given terminal status. */
function stubDetail(
  runId: string,
  status: string,
  {
    issues = [],
    retryable = false,
  }: { issues?: object[]; retryable?: boolean } = {},
) {
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
        retryable,
      }),
    ),
  );
}

beforeEach(() => {
  mockActiveOps = [];
  mockRunCompleted.mockReset();
  __resetRunToastLedger();
});
afterEach(() => {
  vi.restoreAllMocks();
});

describe("OperationsProvider terminal surfacing", () => {
  it("does not retro-toast a run that predates mount", async () => {
    // No running ops at mount: a pre-existing terminal run is simply absent from
    // the running set, so it can never be diffed into a toast.
    mockActiveOps = [];
    renderWithProviders(<OperationsProvider>{null}</OperationsProvider>);

    await new Promise((r) => setTimeout(r, 20));
    expect(mockRunCompleted).not.toHaveBeenCalled();
  });

  it("announces a failure (retryable → Retry action) when a run errors", async () => {
    stubDetail("run-1", "error", {
      issues: [{ connector_playlist_identifier: "pl1" }],
      retryable: true,
    });
    mockActiveOps = [runningRow("run-1", "op-1")];
    const { rerender } = renderWithProviders(
      <OperationsProvider>{null}</OperationsProvider>,
    );

    // run-1 leaves the running set → terminal status resolved as error.
    mockActiveOps = [];
    rerender(<OperationsProvider>{null}</OperationsProvider>);

    await waitFor(() => {
      expect(mockRunCompleted).toHaveBeenCalledWith(
        expect.objectContaining({
          runId: "run-1",
          failed: true,
          action: expect.objectContaining({ label: "Retry failed only" }),
        }),
      );
    });
  });

  it("announces a success when a run completes cleanly", async () => {
    stubDetail("run-2", "complete");
    mockActiveOps = [runningRow("run-2", "op-2")];
    const { rerender } = renderWithProviders(
      <OperationsProvider>{null}</OperationsProvider>,
    );

    mockActiveOps = [];
    rerender(<OperationsProvider>{null}</OperationsProvider>);

    await waitFor(() => {
      expect(mockRunCompleted).toHaveBeenCalledWith(
        expect.objectContaining({ failed: false }),
      );
    });
  });

  it("does not announce a cancelled/superseded run", async () => {
    stubDetail("run-4", "cancelled");
    mockActiveOps = [runningRow("run-4", "op-4")];
    const { rerender } = renderWithProviders(
      <OperationsProvider>{null}</OperationsProvider>,
    );

    mockActiveOps = [];
    rerender(<OperationsProvider>{null}</OperationsProvider>);

    await new Promise((r) => setTimeout(r, 20));
    expect(mockRunCompleted).not.toHaveBeenCalled();
  });

  it("skips a run already claimed by a foreground card", async () => {
    stubDetail("run-3", "error", {
      issues: [{ connector_playlist_identifier: "pl1" }],
    });
    // A foreground card claimed this run's toast first.
    claimRunToast("run-3");

    mockActiveOps = [runningRow("run-3", "op-3")];
    const { rerender } = renderWithProviders(
      <OperationsProvider>{null}</OperationsProvider>,
    );
    mockActiveOps = [];
    rerender(<OperationsProvider>{null}</OperationsProvider>);

    await new Promise((r) => setTimeout(r, 20));
    expect(mockRunCompleted).not.toHaveBeenCalled();
  });
});
