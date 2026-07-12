import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { OperationProgress } from "#/hooks/useOperationProgress";
import { renderWithProviders } from "#/test/test-utils";

import { OperationProgressCard } from "./OperationProgressCard";
import {
  isOperationStartedResult,
  type OperationStartedResult,
} from "./operation-progress-types";

// Mock the SSE hook so tests never open a real EventSource — we drive the
// rendered progress purely from its return value.
const useOperationProgress = vi.hoisted(() => vi.fn());
vi.mock("#/hooks/useOperationProgress", () => ({ useOperationProgress }));

function makeProgress(
  over: Partial<OperationProgress> = {},
): OperationProgress {
  return {
    status: "running",
    current: 40,
    total: 100,
    message: "Importing plays…",
    description: "Import Last.fm history",
    completionPercentage: 40,
    itemsPerSecond: null,
    etaSeconds: null,
    counts: null,
    subOperation: null,
    subOperationHistory: {},
    ...over,
  };
}

const RESULT: OperationStartedResult = {
  status: "operation_started",
  operation_id: "op-123",
  run_id: "run-456",
  description: "Import Last.fm history",
};

function mockProgress(progress: OperationProgress | null) {
  useOperationProgress.mockReturnValue({
    progress,
    isActive: progress?.status === "running" || progress?.status === "pending",
    isConnected: true,
    error: null,
  });
}

beforeEach(() => {
  useOperationProgress.mockReset();
});

describe("OperationProgressCard", () => {
  it("renders the description heading and running progress", () => {
    mockProgress(makeProgress());
    renderWithProviders(<OperationProgressCard result={RESULT} />);

    expect(screen.getByText("Import Last.fm history")).toBeInTheDocument();
    expect(screen.getByText("Importing plays…")).toBeInTheDocument();
    expect(screen.getByText("40/100")).toBeInTheDocument();
    // Subscribed to the operation from the result.
    expect(useOperationProgress).toHaveBeenCalledWith("op-123");
  });

  it("renders a terminal (completed) state", () => {
    mockProgress(
      makeProgress({
        status: "completed",
        message: "Complete",
        completionPercentage: 100,
      }),
    );
    renderWithProviders(<OperationProgressCard result={RESULT} />);

    expect(screen.getByText("Complete")).toBeInTheDocument();
  });

  it("deep-links Open run to the import-history row when a run_id exists", () => {
    mockProgress(makeProgress());
    renderWithProviders(<OperationProgressCard result={RESULT} />);

    expect(screen.getByRole("link", { name: /open run/i })).toHaveAttribute(
      "href",
      "/settings/imports?run=run-456",
    );
  });

  it("omits Open run when no run_id was recorded", () => {
    mockProgress(makeProgress());
    renderWithProviders(
      <OperationProgressCard result={{ ...RESULT, run_id: null }} />,
    );

    expect(
      screen.queryByRole("link", { name: /open run/i }),
    ).not.toBeInTheDocument();
  });

  it("shows a connecting placeholder before the first event", () => {
    mockProgress(null);
    renderWithProviders(<OperationProgressCard result={RESULT} />);

    expect(screen.getByText(/connecting/i)).toBeInTheDocument();
  });
});

describe("isOperationStartedResult", () => {
  it("accepts a valid operation_started result and rejects others", () => {
    expect(isOperationStartedResult(RESULT)).toBe(true);
    expect(isOperationStartedResult({ ...RESULT, run_id: null })).toBe(true);
    expect(isOperationStartedResult({ status: "operation_started" })).toBe(
      false,
    );
    expect(
      isOperationStartedResult({ status: "valid", operation_id: "x" }),
    ).toBe(false);
    expect(isOperationStartedResult({ operation_id: 123 })).toBe(false);
    expect(isOperationStartedResult(null)).toBe(false);
    expect(isOperationStartedResult("operation_started")).toBe(false);
  });
});
