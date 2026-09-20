import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { FavoriteToggle } from "./FavoriteToggle";

describe("FavoriteToggle", () => {
  it("labels the action the click will perform", () => {
    const { rerender } = render(
      <FavoriteToggle isFavorited={false} onToggle={() => {}} label="Bowie" />,
    );

    const button = screen.getByRole("button", { name: "Favorite Bowie" });
    expect(button).toHaveAttribute("aria-pressed", "false");

    rerender(<FavoriteToggle isFavorited onToggle={() => {}} label="Bowie" />);

    expect(
      screen.getByRole("button", { name: "Unfavorite Bowie" }),
    ).toHaveAttribute("aria-pressed", "true");
  });

  it("falls back to a bare label when no entity name is given", () => {
    render(<FavoriteToggle isFavorited={false} onToggle={() => {}} />);

    expect(screen.getByRole("button", { name: "Favorite" })).toBeVisible();
  });

  it("toggles without triggering the surrounding row", async () => {
    const user = userEvent.setup();
    const onToggle = vi.fn();
    const onRowClick = vi.fn();

    // The row listener has to sit ABOVE the React root: React delegates from
    // the root container, so a listener on that same node fires regardless of
    // `stopPropagation` and would make this assertion meaningless.
    const row = document.createElement("div");
    row.addEventListener("click", onRowClick);
    const mount = document.createElement("div");
    row.appendChild(mount);
    document.body.appendChild(row);

    render(
      <FavoriteToggle isFavorited={false} onToggle={onToggle} label="Bowie" />,
      { container: mount },
    );

    await user.click(screen.getByRole("button", { name: "Favorite Bowie" }));

    expect(onToggle).toHaveBeenCalledTimes(1);
    expect(onRowClick).not.toHaveBeenCalled();
  });

  it("does not fire while disabled", async () => {
    const user = userEvent.setup();
    const onToggle = vi.fn();

    render(
      <FavoriteToggle
        isFavorited={false}
        onToggle={onToggle}
        disabled
        label="Bowie"
      />,
    );

    const button = screen.getByRole("button", { name: "Favorite Bowie" });
    expect(button).toBeDisabled();
    await user.click(button);

    expect(onToggle).not.toHaveBeenCalled();
  });
});
