import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { NODE_CONFIG } from "#/lib/workflow-config";
import { NodeTypeBadge } from "./NodeTypeBadge";

describe("NodeTypeBadge", () => {
  it("shows the category label tinted with the canvas accent color", () => {
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
});
