import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { getStatusConfig, RunStatusBadge } from "./RunStatusBadge";

describe("RunStatusBadge", () => {
  it.each([
    "pending",
    "running",
    "completed",
    "failed",
    "crashed",
    "cancelled",
  ])("renders %s status", (status) => {
    render(<RunStatusBadge status={status} />);
    const config = getStatusConfig(status);
    expect(screen.getByText(config.label)).toBeInTheDocument();
  });

  it("falls back to pending for unknown status", () => {
    render(<RunStatusBadge status="unknown_status" />);
    expect(screen.getByText("Pending")).toBeInTheDocument();
  });

  it("applies additional className", () => {
    const { container } = render(
      <RunStatusBadge status="completed" className="mt-1" />,
    );
    const badge = container.querySelector("span");
    expect(badge?.className).toContain("mt-1");
  });
});

describe("getStatusConfig", () => {
  it("returns correct label for each status", () => {
    expect(getStatusConfig("pending").label).toBe("Pending");
    expect(getStatusConfig("running").label).toBe("Running");
    expect(getStatusConfig("completed").label).toBe("Completed");
    expect(getStatusConfig("failed").label).toBe("Failed");
    expect(getStatusConfig("crashed").label).toBe("Crashed");
    expect(getStatusConfig("cancelled").label).toBe("Cancelled");
  });

  it("returns pending config for unknown status", () => {
    expect(getStatusConfig("not_a_real_status")).toEqual(
      getStatusConfig("pending"),
    );
  });
});
