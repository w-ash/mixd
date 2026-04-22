import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { DismissibleChip } from "./DismissibleChip";

describe("DismissibleChip", () => {
  it("renders the label", () => {
    render(<DismissibleChip label="mood:chill" />);
    expect(screen.getByText("mood:chill")).toBeInTheDocument();
  });

  it("omits the remove button when onRemove is not provided", () => {
    render(<DismissibleChip label="read-only" />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("renders a remove button when onRemove is provided", async () => {
    const onRemove = vi.fn();
    render(<DismissibleChip label="mood:chill" onRemove={onRemove} />);
    await userEvent.click(
      screen.getByRole("button", { name: "Remove mood:chill" }),
    );
    expect(onRemove).toHaveBeenCalledTimes(1);
  });

  it("applies font-mono when fontVariant='mono'", () => {
    render(<DismissibleChip label="abc123" fontVariant="mono" />);
    // The badge is the innermost element carrying 'font-mono'.
    const badge = screen.getByText("abc123").closest("[class*='font-mono']");
    expect(badge).not.toBeNull();
  });

  it("does NOT apply font-mono when fontVariant is 'display' (default)", () => {
    const { container } = render(<DismissibleChip label="Liked" />);
    expect(container.querySelector(".font-mono")).toBeNull();
  });

  it("honors a custom ariaRemoveLabel", () => {
    render(
      <DismissibleChip
        label="Starred"
        onRemove={vi.fn()}
        ariaRemoveLabel="Clear preference filter"
      />,
    );
    expect(
      screen.getByRole("button", { name: "Clear preference filter" }),
    ).toBeInTheDocument();
  });
});
