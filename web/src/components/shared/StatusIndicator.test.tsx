import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  confidenceVariant,
  StatusIndicator,
  syncStatusVariant,
} from "./StatusIndicator";

describe("StatusIndicator", () => {
  it("renders label text", () => {
    render(<StatusIndicator variant="success" label="Synced" />);

    expect(screen.getByText("Synced")).toBeInTheDocument();
  });

  it("renders detail text when provided", () => {
    render(<StatusIndicator variant="info" label="Syncing" detail="3 of 10" />);

    expect(screen.getByText("Syncing")).toBeInTheDocument();
    expect(screen.getByText("3 of 10")).toBeInTheDocument();
  });

  it("omits detail text when not provided", () => {
    const { container } = render(
      <StatusIndicator variant="success" label="Done" />,
    );

    // Only the label span and the icon — no detail span
    const spans = container.querySelectorAll("span > span");
    expect(spans).toHaveLength(1);
  });

  it("renders success variant with label", () => {
    render(<StatusIndicator variant="success" label="Connected" />);

    expect(screen.getByText("Connected")).toBeInTheDocument();
  });

  it("renders warning variant with label", () => {
    render(<StatusIndicator variant="warning" label="Weak match" />);

    expect(screen.getByText("Weak match")).toBeInTheDocument();
  });

  it("renders error variant with label", () => {
    render(<StatusIndicator variant="error" label="Failed" />);

    expect(screen.getByText("Failed")).toBeInTheDocument();
  });

  it("renders info variant with label", () => {
    render(<StatusIndicator variant="info" label="Syncing" />);

    expect(screen.getByText("Syncing")).toBeInTheDocument();
  });

  it("renders neutral variant with label", () => {
    render(<StatusIndicator variant="neutral" label="Unknown" />);

    expect(screen.getByText("Unknown")).toBeInTheDocument();
  });

  it("renders an icon for each variant", () => {
    const { container } = render(
      <StatusIndicator variant="success" label="OK" />,
    );

    expect(container.querySelector("svg")).toBeInTheDocument();
  });
});

describe("confidenceVariant", () => {
  it("returns success for confidence >= 80", () => {
    expect(confidenceVariant(80)).toBe("success");
    expect(confidenceVariant(100)).toBe("success");
    expect(confidenceVariant(95)).toBe("success");
  });

  it("returns warning for confidence >= 50 and < 80", () => {
    expect(confidenceVariant(50)).toBe("warning");
    expect(confidenceVariant(79)).toBe("warning");
    expect(confidenceVariant(65)).toBe("warning");
  });

  it("returns error for confidence < 50", () => {
    expect(confidenceVariant(49)).toBe("error");
    expect(confidenceVariant(0)).toBe("error");
    expect(confidenceVariant(25)).toBe("error");
  });
});

describe("syncStatusVariant", () => {
  it("returns success for synced", () => {
    expect(syncStatusVariant("synced")).toBe("success");
  });

  it("returns info for syncing", () => {
    expect(syncStatusVariant("syncing")).toBe("info");
  });

  it("returns error for error", () => {
    expect(syncStatusVariant("error")).toBe("error");
  });

  it("returns neutral for unknown status", () => {
    expect(syncStatusVariant("pending")).toBe("neutral");
    expect(syncStatusVariant("")).toBe("neutral");
    expect(syncStatusVariant("other")).toBe("neutral");
  });
});
