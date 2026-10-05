import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { getStatusConfig, RunStatusBadge } from "./RunStatusBadge";

describe("RunStatusBadge", () => {
  it.each([
    ["pending", "Pending"],
    ["queued", "Queued"],
    ["running", "Running"],
    ["completed", "Completed"],
    ["failed", "Failed"],
    ["crashed", "Crashed"],
    ["complete", "Complete"],
    ["partial", "Completed with issues"],
    ["error", "Error"],
    ["cancelled", "Cancelled"],
  ])("renders the %s status as %s", (status, label) => {
    const { container } = render(<RunStatusBadge status={status} />);
    // Exact match: "Complete" must not pass for "Completed" or vice versa.
    expect(container.textContent).toBe(label);
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
    expect(getStatusConfig("complete").label).toBe("Complete");
    expect(getStatusConfig("partial").label).toBe("Completed with issues");
    expect(getStatusConfig("error").label).toBe("Error");
    expect(getStatusConfig("cancelled").label).toBe("Cancelled");
  });
});
