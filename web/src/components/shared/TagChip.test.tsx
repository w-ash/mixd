import { describe, expect, it, vi } from "vitest";

import { renderWithProviders, screen, userEvent } from "#/test/test-utils";

import { TagChip } from "./TagChip";

describe("TagChip", () => {
  it("renders the tag text", () => {
    renderWithProviders(<TagChip tag="mood:chill" />);
    expect(screen.getByText("mood:chill")).toBeInTheDocument();
  });

  it("omits remove button in read-only mode", () => {
    renderWithProviders(<TagChip tag="mood:chill" />);
    expect(
      screen.queryByRole("button", { name: /remove/i }),
    ).not.toBeInTheDocument();
  });

  it("renders remove button when onRemove is provided", () => {
    renderWithProviders(<TagChip tag="mood:chill" onRemove={vi.fn()} />);
    expect(
      screen.getByRole("button", { name: "Remove mood:chill" }),
    ).toBeInTheDocument();
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
