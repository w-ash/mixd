import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { NODE_CONFIG } from "#/lib/workflow-config";
import { NodeTypeBadge } from "./NodeTypeBadge";

describe("NodeTypeBadge", () => {
  it("shows the shared category label for a dotted node type", () => {
    render(<NodeTypeBadge nodeType="source.liked_tracks" />);
    expect(screen.getByText("Source")).toBeInTheDocument();
  });

  it("tints the badge with the category accent color", () => {
    const { container } = render(
      <NodeTypeBadge nodeType="filter.play_count" />,
    );
    const badge = container.querySelector("span");
    expect(badge).toHaveTextContent("Filter");
    expect(badge).toHaveStyle({ color: NODE_CONFIG.filter.accentColor });
  });

  it("falls back to muted styling and the raw prefix for unknown categories", () => {
    const { container } = render(<NodeTypeBadge nodeType="unknown.thing" />);
    const badge = container.querySelector("span");
    expect(badge).toHaveTextContent("unknown");
    expect(badge?.className).toContain("bg-surface-elevated");
  });

  it("handles a node type without dots", () => {
    render(<NodeTypeBadge nodeType="destination" />);
    expect(screen.getByText("Destination")).toBeInTheDocument();
  });

  it("applies additional className", () => {
    const { container } = render(
      <NodeTypeBadge nodeType="enricher.metadata" className="ml-2" />,
    );
    expect(container.querySelector("span")?.className).toContain("ml-2");
  });
});
