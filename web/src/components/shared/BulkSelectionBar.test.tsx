import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { BulkSelectionBar } from "./BulkSelectionBar";

describe("BulkSelectionBar", () => {
  it("renders the count, the caller's actions, and Clear", async () => {
    const onClear = vi.fn();
    render(
      <BulkSelectionBar count={3} onClear={onClear}>
        <button type="button">Tag selected</button>
      </BulkSelectionBar>,
    );

    const bar = screen.getByRole("region", { name: "Bulk selection" });
    expect(bar).toHaveTextContent("3 selected");
    expect(screen.getByRole("button", { name: "Tag selected" })).toBeVisible();

    await userEvent.click(
      screen.getByRole("button", { name: "Clear selection" }),
    );
    expect(onClear).toHaveBeenCalledOnce();
  });

  it("pluralizes an optional noun", () => {
    const { rerender } = render(
      <BulkSelectionBar count={1} noun="track" onClear={vi.fn()} />,
    );
    expect(screen.getByRole("region")).toHaveTextContent("1 track selected");

    rerender(<BulkSelectionBar count={2} noun="track" onClear={vi.fn()} />);
    expect(screen.getByRole("region")).toHaveTextContent("2 tracks selected");
  });

  it("renders nothing when nothing is selected", () => {
    const { container } = render(
      <BulkSelectionBar count={0} onClear={vi.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
