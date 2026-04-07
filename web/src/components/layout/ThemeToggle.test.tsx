import { describe, expect, it } from "vitest";

import { renderWithProviders, screen, userEvent } from "#/test/test-utils";

import { ThemeToggle } from "./ThemeToggle";

describe("ThemeToggle", () => {
  it("renders with an accessible label", () => {
    renderWithProviders(<ThemeToggle />);

    expect(
      screen.getByRole("button", { name: /switch to/i }),
    ).toBeInTheDocument();
  });

  it("cycles through modes on click", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ThemeToggle />);

    const button = screen.getByRole("button", { name: /switch to/i });

    // Default is "dark" → label says "Switch to light mode"
    expect(button).toHaveAttribute("aria-label", "Switch to light mode");

    // Click: dark → light
    await user.click(button);
    expect(button).toHaveAttribute("aria-label", "Switch to system theme");

    // Click: light → system
    await user.click(button);
    expect(button).toHaveAttribute("aria-label", "Switch to dark mode");

    // Click: system → dark
    await user.click(button);
    expect(button).toHaveAttribute("aria-label", "Switch to light mode");
  });
});
