import { describe, expect, it } from "vitest";

import { renderWithProviders, screen } from "@/test/test-utils";

import { BackLink } from "./BackLink";

describe("BackLink", () => {
  it("renders a link with arrow icon and children", () => {
    renderWithProviders(<BackLink to="/library">Library</BackLink>);

    const link = screen.getByRole("link", { name: /Library/ });
    expect(link).toHaveAttribute("href", "/library");
  });

  it("renders with dynamic path and label", () => {
    renderWithProviders(<BackLink to="/workflows/42">My Workflow</BackLink>);

    const link = screen.getByRole("link", { name: /My Workflow/ });
    expect(link).toHaveAttribute("href", "/workflows/42");
  });
});
