import { describe, expect, it } from "vitest";

import { renderWithProviders, screen } from "#/test/test-utils";

import { BackLink } from "./BackLink";

describe("BackLink", () => {
  it("links to the given path under the given label", () => {
    renderWithProviders(<BackLink to="/workflows/42">My Workflow</BackLink>);

    const link = screen.getByRole("link", { name: /My Workflow/ });
    expect(link).toHaveAttribute("href", "/workflows/42");
  });
});
