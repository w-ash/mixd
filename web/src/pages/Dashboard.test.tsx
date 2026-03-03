import { describe, expect, it } from "vitest";

import { renderWithProviders, screen } from "@/test/test-utils";

import { Dashboard } from "./Dashboard";

describe("Dashboard", () => {
  it("renders the app title", () => {
    renderWithProviders(<Dashboard />);

    expect(screen.getByText("narada")).toBeInTheDocument();
  });

  it("renders the tagline", () => {
    renderWithProviders(<Dashboard />);

    expect(screen.getByText("Personal music metadata hub")).toBeInTheDocument();
  });
});
