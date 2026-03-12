import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { NodeTypeBadge } from "./NodeTypeBadge";

describe("NodeTypeBadge", () => {
  it("extracts and displays the category from node type", () => {
    render(<NodeTypeBadge nodeType="source.liked_tracks" />);
    expect(screen.getByText("source")).toBeInTheDocument();
  });

  it("renders known categories with specific styles", () => {
    const { container } = render(<NodeTypeBadge nodeType="filter.play_count" />);
    const badge = container.querySelector("span");
    expect(badge).toHaveTextContent("filter");
    // Filter category has oklch(0.35_0.08_55) background
    expect(badge?.className).toContain("bg-[oklch(0.35_0.08_55)]");
  });

  it("uses fallback style for unknown categories", () => {
    const { container } = render(<NodeTypeBadge nodeType="unknown.thing" />);
    const badge = container.querySelector("span");
    expect(badge).toHaveTextContent("unknown");
    expect(badge?.className).toContain("bg-surface-elevated");
  });

  it("handles node type without dots", () => {
    render(<NodeTypeBadge nodeType="source" />);
    expect(screen.getByText("source")).toBeInTheDocument();
  });

  it("applies additional className", () => {
    const { container } = render(
      <NodeTypeBadge nodeType="enricher.metadata" className="ml-2" />,
    );
    const badge = container.querySelector("span");
    expect(badge?.className).toContain("ml-2");
  });
});
