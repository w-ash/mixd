import { describe, expect, it, vi } from "vitest";

import { renderWithProviders, screen, userEvent } from "#/test/test-utils";

import { TagChip } from "./TagChip";

describe("TagChip", () => {
  it("shows the tag in mono type with no remove button when read-only", () => {
    renderWithProviders(<TagChip tag="mood:chill" />);

    // Tags are user-authored identifiers, so they render monospace.
    expect(screen.getByText("mood:chill").parentElement).toHaveClass(
      "font-mono",
    );
    expect(
      screen.queryByRole("button", { name: /remove/i }),
    ).not.toBeInTheDocument();
  });

  it("calls onRemove when the button is clicked", async () => {
    const onRemove = vi.fn();
    renderWithProviders(<TagChip tag="mood:chill" onRemove={onRemove} />);

    await userEvent.click(
      screen.getByRole("button", { name: "Remove mood:chill" }),
    );

    expect(onRemove).toHaveBeenCalledOnce();
  });
});
