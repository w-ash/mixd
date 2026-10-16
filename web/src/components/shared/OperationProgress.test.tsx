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
    counts: null,
    subOperation: null,
    subOperationHistory: {},
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

  it.each([
    ["pending", "Waiting"],
    ["running", "Running"],
    ["reconnecting", "Reconnecting"],
    ["completed", "Complete"],
    ["failed", "Failed"],
    ["cancelled", "Cancelled"],
  ] as const)(
    "announces the %s status as %s with the message",
    (status, label) => {
      render(
        <OperationProgress
          progress={makeProgress({ status, message: "Syncing playlist" })}
        />,
      );

      expect(
        screen.getByRole("status", {
          name: `Operation ${label}: Syncing playlist`,
        }),
      ).toHaveTextContent("Syncing playlist");
    },
  );

  it("renders rate and ETA when available", () => {
    render(
      <OperationProgress
        progress={makeProgress({
          itemsPerSecond: 2.5,
          etaSeconds: 90,
        })}
      />,
    );

    expect(screen.getByText("2.5/sec")).toBeInTheDocument();
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
            itemOperationId: "sub-1",
            description: "Fetching metadata",
            current: 25,
            total: 50,
            message: "Processed 25/50",
            phase: "enrich",
            completionPercentage: 50,
            connectorPlaylistIdentifier: null,
            playlistName: null,
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
            itemOperationId: "sub-1",
            description: "Fetching playlist",
            current: 0,
            total: null,
            message: "Fetching playlist from Spotify",
            phase: "fetch",
            completionPercentage: null,
            connectorPlaylistIdentifier: null,
            playlistName: null,
          },
        })}
      />,
    );

    expect(
      screen.getByText("Fetching playlist from Spotify"),
    ).toBeInTheDocument();
    // An unknown total shows the message only, with no count.
    expect(
      screen.getByRole("status", {
        name: "Sub-operation: Fetching playlist from Spotify",
      }),
    ).toHaveTextContent(/^Fetching playlist from Spotify$/);
  });

  it("does not render sub-operation when null", () => {
    render(
      <OperationProgress progress={makeProgress({ subOperation: null })} />,
    );

    expect(screen.queryByLabelText(/Sub-operation/)).not.toBeInTheDocument();
  });
});
