import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TooltipProvider } from "#/components/ui/tooltip";
import { UnmatchedBadge } from "./UnmatchedBadge";

function renderBadge(count: number | null | undefined) {
  return render(
    <TooltipProvider>
      <UnmatchedBadge count={count} />
    </TooltipProvider>,
  );
}

describe("UnmatchedBadge", () => {
  it("shows the count when there are unmatched tracks", () => {
    renderBadge(2);
    expect(screen.getByText("2 unmatched")).toBeInTheDocument();
  });

  it("renders nothing when there are no unmatched tracks", () => {
    const { container } = renderBadge(0);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing for a nullish count", () => {
    const { container } = renderBadge(null);
    expect(container).toBeEmptyDOMElement();
  });

  it("wires a keyboard-focusable trigger for the reason tooltip", () => {
    renderBadge(3);

    // The chip is the tooltip trigger: focusable (tabIndex) and Radix-wired
    // (data-state), so the reassurance is reachable by keyboard, not mouse-only.
    const badge = screen.getByText("3 unmatched");
    expect(badge).toHaveAttribute("tabindex", "0");
    expect(badge).toHaveAttribute("data-state", "closed");
  });
});
