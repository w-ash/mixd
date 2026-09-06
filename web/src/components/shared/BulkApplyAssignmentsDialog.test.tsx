import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { OperationProgress } from "#/hooks/useOperationProgress";
import { __resetRunToastLedger } from "#/lib/operation-toast-ledger";
import { server } from "#/test/setup";
import {
  renderWithProviders,
  screen,
  userEvent,
  waitFor,
} from "#/test/test-utils";

import { BulkApplyAssignmentsDialog } from "./BulkApplyAssignmentsDialog";

// The dialog's whole job downstream of the POST is driven by the SSE stream, so
// tests hand it a progress fixture rather than opening a connection.
let mockProgress: OperationProgress | null = null;
vi.mock("#/hooks/useOperationProgress", async () => {
  const actual = await vi.importActual<
    typeof import("#/hooks/useOperationProgress")
  >("#/hooks/useOperationProgress");
  return {
    ...actual,
    useOperationProgress: (operationId: string | null) => ({
      progress: operationId ? mockProgress : null,
      isActive: mockProgress?.status === "running",
    }),
  };
});

const mockRunCompleted = vi.fn();
vi.mock("#/lib/toasts", async () => {
  const actual =
    await vi.importActual<typeof import("#/lib/toasts")>("#/lib/toasts");
  return {
    ...actual,
    toasts: {
      ...actual.toasts,
      runCompleted: (...args: unknown[]) => mockRunCompleted(...args),
    },
  };
});

function progress(overrides: Partial<OperationProgress> = {}) {
  return {
    status: "completed",
    current: 5,
    total: 5,
    message: "Complete",
    description: null,
    completionPercentage: 100,
    itemsPerSecond: null,
    etaSeconds: null,
    counts: { tracks: 5, errors: 1 },
    subOperation: null,
    subOperationHistory: {},
    ...overrides,
  } satisfies OperationProgress;
}

function mockApply202(runId: string | null = "run-1") {
  server.use(
    http.post("*/api/v1/playlist-assignments/apply-bulk", () =>
      HttpResponse.json(
        { operation_id: "op-1", run_id: runId },
        { status: 202 },
      ),
    ),
  );
}

beforeEach(() => {
  mockProgress = null;
  mockRunCompleted.mockReset();
  __resetRunToastLedger();
});

function setup() {
  return renderWithProviders(
    <BulkApplyAssignmentsDialog open onOpenChange={vi.fn()} />,
  );
}

describe("BulkApplyAssignmentsDialog", () => {
  it("announces a partial run as completed-with-issues once it concludes", async () => {
    mockApply202();
    const { rerender } = setup();

    await userEvent.click(screen.getByRole("button", { name: "Apply" }));

    mockProgress = progress();
    rerender(<BulkApplyAssignmentsDialog open onOpenChange={vi.fn()} />);

    await waitFor(() => {
      expect(mockRunCompleted).toHaveBeenCalledWith(
        expect.objectContaining({
          operationType: "apply_assignments_bulk",
          issueCount: 1,
          failed: false,
          runId: "run-1",
        }),
      );
    });
    expect(mockRunCompleted).toHaveBeenCalledOnce();
    // Terminal phase: the confirm button becomes a way out, not a re-run.
    expect(screen.queryByRole("button", { name: "Apply" })).toBeNull();
  });

  it("stays quiet for a cancelled run, matching the global watcher", async () => {
    mockApply202();
    const { rerender } = setup();

    await userEvent.click(screen.getByRole("button", { name: "Apply" }));

    mockProgress = progress({ status: "cancelled", message: "Cancelled" });
    rerender(<BulkApplyAssignmentsDialog open onOpenChange={vi.fn()} />);

    await waitFor(() => {
      expect(screen.queryByRole("button", { name: "Apply" })).toBeNull();
    });
    expect(mockRunCompleted).not.toHaveBeenCalled();
  });
});
