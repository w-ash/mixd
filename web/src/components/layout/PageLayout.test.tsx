import { describe, expect, it } from "vitest";

import { mockMatchMedia, renderWithProviders, screen } from "#/test/test-utils";

import { PageLayout } from "./PageLayout";

describe("PageLayout", () => {
  it("renders the desktop shell at-or-above lg breakpoint", () => {
    mockMatchMedia(1280);
    renderWithProviders(<PageLayout />);

    expect(
      screen.getByRole("navigation", { name: /main navigation/i }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("navigation", { name: /mobile navigation/i }),
    ).not.toBeInTheDocument();
  });

  it("renders the mobile shell below lg breakpoint", () => {
    mockMatchMedia(390);
    renderWithProviders(<PageLayout />);

    expect(
      screen.getByRole("navigation", { name: /mobile navigation/i }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("navigation", { name: /main navigation/i }),
    ).not.toBeInTheDocument();
  });

  it("treats iPad portrait (820px) as mobile", () => {
    mockMatchMedia(820);
    renderWithProviders(<PageLayout />);

    expect(
      screen.getByRole("navigation", { name: /mobile navigation/i }),
    ).toBeInTheDocument();
  });
});
