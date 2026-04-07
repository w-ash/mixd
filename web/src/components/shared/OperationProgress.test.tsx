import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { OperationProgress as OperationProgressData } from "#/hooks/useOperationProgress";

import { OperationProgress } from "./OperationProgress";

function makeProgress(
  overrides: Partial<OperationProgressData> = {},
): OperationProgressData {
  return {
    status: "running",
    current: 50,
    total: 100,
    message: "Processing...",
    description: null,
    completionPercentage: 50,
    itemsPerSecond: null,
    etaSeconds: null,
    subOperation: null,
    ...overrides,
  };
}

describe("OperationProgress", () => {
  it("renders running state with message and percentage", () => {
    render(<OperationProgress progress={makeProgress()} />);

    expect(screen.getByText("Processing...")).toBeInTheDocument();
    expect(screen.getByText("50/100")).toBeInTheDocument();
    expect(screen.getByText("50%")).toBeInTheDocument();
  });

  it("renders pending state", () => {
    render(
      <OperationProgress
        progress={makeProgress({ status: "pending", message: "Connecting..." })}
      />,
    );

    expect(screen.getByText("Connecting...")).toBeInTheDocument();
  });

  it("renders completed state", () => {
    render(
      <OperationProgress
        progress={makeProgress({
          status: "completed",
          message: "Complete",
          completionPercentage: 100,
        })}
      />,
    );

    expect(screen.getByText("Complete")).toBeInTheDocument();
    expect(screen.getByText("100%")).toBeInTheDocument();
  });

  it("renders failed state", () => {
    render(
      <OperationProgress
        progress={makeProgress({
          status: "failed",
          message: "Operation failed",
        })}
      />,
    );

    expect(screen.getByText("Operation failed")).toBeInTheDocument();
  });

  it("renders rate and ETA when available", () => {
    render(
      <OperationProgress
        progress={makeProgress({
          itemsPerSecond: 2.5,
          etaSeconds: 90,
        })}
      />,
    );

    expect(screen.getByText("2.5/s")).toBeInTheDocument();
    expect(screen.getByText("~1m 30s")).toBeInTheDocument();
  });

  it("hides ETA for terminal states", () => {
    render(
      <OperationProgress
        progress={makeProgress({
          status: "completed",
          etaSeconds: 10,
        })}
      />,
    );

    expect(screen.queryByText(/~10s/)).not.toBeInTheDocument();
  });

  it("has accessible aria-label", () => {
    render(<OperationProgress progress={makeProgress()} />);

    const output = screen.getByLabelText("Operation Running: Processing...");
    expect(output).toBeInTheDocument();
  });

  it("formats slow rates as per-minute", () => {
    render(
      <OperationProgress progress={makeProgress({ itemsPerSecond: 0.5 })} />,
    );

    expect(screen.getByText("30.0/min")).toBeInTheDocument();
  });

  it("renders sub-operation progress bar when present", () => {
    render(
      <OperationProgress
        progress={makeProgress({
          subOperation: {
            operationId: "sub-1",
            description: "Fetching metadata",
            current: 25,
            total: 50,
            message: "Processed 25/50",
            phase: "enrich",
            completionPercentage: 50,
          },
        })}
      />,
    );

    expect(screen.getByText("Processed 25/50")).toBeInTheDocument();
    expect(screen.getByText("25/50")).toBeInTheDocument();
  });

  it("renders indeterminate sub-operation without count", () => {
    render(
      <OperationProgress
        progress={makeProgress({
          subOperation: {
            operationId: "sub-1",
            description: "Fetching playlist",
            current: 0,
            total: null,
            message: "Fetching playlist from Spotify",
            phase: "fetch",
            completionPercentage: null,
          },
        })}
      />,
    );

    expect(
      screen.getByText("Fetching playlist from Spotify"),
    ).toBeInTheDocument();
    // Should NOT show a count within the sub-operation section
    const subOutput = screen.getByLabelText(
      "Sub-operation: Fetching playlist from Spotify",
    );
    expect(subOutput.querySelector(".font-mono")).not.toBeInTheDocument();
  });

  it("does not render sub-operation when null", () => {
    render(
      <OperationProgress progress={makeProgress({ subOperation: null })} />,
    );

    expect(screen.queryByLabelText(/Sub-operation/)).not.toBeInTheDocument();
  });
});
