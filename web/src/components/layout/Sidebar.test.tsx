import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { server } from "@/test/setup";
import { renderWithProviders, screen } from "@/test/test-utils";

import { Sidebar } from "./Sidebar";

describe("Sidebar", () => {
  it("renders all navigation links", () => {
    renderWithProviders(<Sidebar />);

    expect(screen.getByText("Dashboard")).toBeInTheDocument();
    expect(screen.getByText("Library")).toBeInTheDocument();
    expect(screen.getByText("Playlists")).toBeInTheDocument();
    expect(screen.getByText("Workflows")).toBeInTheDocument();
    expect(screen.getByText("Settings")).toBeInTheDocument();
  });

  it("renders the brand name", () => {
    renderWithProviders(<Sidebar />);
    expect(screen.getByText("mixd")).toBeInTheDocument();
  });

  it("renders settings sub-navigation items", () => {
    renderWithProviders(<Sidebar />, {
      routerProps: { initialEntries: ["/settings/integrations"] },
    });

    expect(screen.getByText("Integrations")).toBeInTheDocument();
    expect(screen.getByText("Sync")).toBeInTheDocument();
  });

  it("has accessible navigation landmark", () => {
    renderWithProviders(<Sidebar />);
    expect(
      screen.getByRole("navigation", { name: /main navigation/i }),
    ).toBeInTheDocument();
  });

  it("highlights active route", () => {
    renderWithProviders(<Sidebar />, {
      routerProps: { initialEntries: ["/playlists"] },
    });

    // The active link should have the primary text color class
    const playlistLink = screen.getByText("Playlists").closest("a");
    expect(playlistLink?.className).toContain("text-primary");
  });

  it("renders version from health API", async () => {
    server.use(
      http.get("*/api/v1/health", () =>
        HttpResponse.json({ status: "healthy", version: "0.5.8" }),
      ),
    );
    renderWithProviders(<Sidebar />);
    expect(await screen.findByText(/v0\.5\.8/)).toBeInTheDocument();
  });

  it("renders theme toggle in footer", () => {
    renderWithProviders(<Sidebar />);
    expect(
      screen.getByRole("button", { name: /switch to/i }),
    ).toBeInTheDocument();
  });
});
