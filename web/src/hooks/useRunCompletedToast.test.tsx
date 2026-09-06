import { renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { __resetRunToastLedger } from "#/lib/operation-toast-ledger";
import { toasts } from "#/lib/toasts";
import type {
  OperationProgress,
  OperationStatus,
} from "./useOperationProgress";

import {
  type RunToastContext,
  useRunCompletedToast,
} from "./useRunCompletedToast";

function wrapper({ children }: { children: ReactNode }) {
  return <MemoryRouter>{children}</MemoryRouter>;
}

function progressWith(
  status: OperationStatus,
  counts: Record<string, unknown> | null = null,
): OperationProgress {
  return {
    status,
    current: 0,
    total: null,
    message: "",
    description: null,
    completionPercentage: null,
    itemsPerSecond: null,
    etaSeconds: null,
    counts,
    subOperation: null,
    subOperationHistory: {},
  };
}

interface Props {
  operationId: string | null;
  runId: string | null;
  progress: OperationProgress | null;
  buildToast?: (context: RunToastContext) => void;
  onTerminal?: (context: RunToastContext) => void;
}

function renderToastHook(initialProps: Props) {
  return renderHook(
    (props: Props) =>
      useRunCompletedToast({ operationType: "sync_likes", ...props }),
    { wrapper, initialProps },
  );
}

describe("useRunCompletedToast", () => {
  beforeEach(() => {
    __resetRunToastLedger();
    vi.spyOn(toasts, "runCompleted").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("stays quiet while the operation is still running", () => {
    renderToastHook({
      operationId: "op-1",
      runId: "run-1",
      progress: progressWith("running"),
    });

    expect(toasts.runCompleted).not.toHaveBeenCalled();
  });

  it("announces a completed run once, with its issue count", () => {
    const { rerender } = renderToastHook({
      operationId: "op-1",
      runId: "run-1",
      progress: progressWith("running"),
    });

    const props: Props = {
      operationId: "op-1",
      runId: "run-1",
      progress: progressWith("completed", { imported: 3, errors: 2 }),
    };
    rerender(props);
    // Re-rendering with the same terminal progress must not re-announce.
    rerender({ ...props });

    expect(toasts.runCompleted).toHaveBeenCalledTimes(1);
    expect(toasts.runCompleted).toHaveBeenCalledWith(
      expect.objectContaining({
        operationType: "sync_likes",
        counts: { imported: 3, errors: 2 },
        issueCount: 2,
        runId: "run-1",
        failed: false,
      }),
    );
  });

  it("marks anything that did not complete as failed", () => {
    renderToastHook({
      operationId: "op-1",
      runId: "run-1",
      progress: progressWith("failed"),
    });

    expect(toasts.runCompleted).toHaveBeenCalledWith(
      expect.objectContaining({ failed: true, issueCount: 0 }),
    );
  });

  it("says nothing about a cancelled run", () => {
    const onTerminal = vi.fn();
    renderToastHook({
      operationId: "op-1",
      runId: "run-1",
      progress: progressWith("cancelled"),
      onTerminal,
    });

    expect(toasts.runCompleted).not.toHaveBeenCalled();
    // The run still concluded, so dependent refreshes still happen.
    expect(onTerminal).toHaveBeenCalledTimes(1);
  });

  it("backs off when another surface already claimed the run", () => {
    const onTerminal = vi.fn();
    renderToastHook({
      operationId: "op-1",
      runId: "run-1",
      progress: progressWith("completed"),
    });
    expect(toasts.runCompleted).toHaveBeenCalledTimes(1);

    // A second surface watching the same run over its own operation id.
    renderToastHook({
      operationId: "op-2",
      runId: "run-1",
      progress: progressWith("completed"),
      onTerminal,
    });

    expect(toasts.runCompleted).toHaveBeenCalledTimes(1);
    expect(onTerminal).toHaveBeenCalledTimes(1);
  });

  it("announces a run with no audit row, without a deep link", () => {
    renderToastHook({
      operationId: "op-1",
      runId: null,
      progress: progressWith("completed"),
    });

    expect(toasts.runCompleted).toHaveBeenCalledWith(
      expect.objectContaining({ runId: null }),
    );
  });

  it("lets a caller replace the toast with its own summary", () => {
    const buildToast = vi.fn();

    renderToastHook({
      operationId: "op-1",
      runId: "run-1",
      progress: progressWith("completed", { errors: 1 }),
      buildToast,
    });

    expect(toasts.runCompleted).not.toHaveBeenCalled();
    expect(buildToast).toHaveBeenCalledWith(
      expect.objectContaining({
        operationId: "op-1",
        runId: "run-1",
        failed: false,
        issueCount: 1,
      }),
    );
  });

  it("announces the next operation after the first has concluded", () => {
    const { rerender } = renderToastHook({
      operationId: "op-1",
      runId: "run-1",
      progress: progressWith("completed"),
    });

    rerender({
      operationId: "op-2",
      runId: "run-2",
      progress: progressWith("running"),
    });
    rerender({
      operationId: "op-2",
      runId: "run-2",
      progress: progressWith("completed"),
    });

    expect(toasts.runCompleted).toHaveBeenCalledTimes(2);
  });
});
