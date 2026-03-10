import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import type { CheckpointStatusSchema } from "@/api/generated/model";
import { server } from "@/test/setup";
import { renderWithProviders, screen } from "@/test/test-utils";

import { Sync } from "./Sync";

const checkpoints: CheckpointStatusSchema[] = [
  {
    service: "spotify",
    entity_type: "likes",
    has_previous_sync: true,
    last_sync_timestamp: "2025-12-01T10:00:00Z",
  },
  {
    service: "lastfm",
    entity_type: "plays",
    has_previous_sync: false,
    last_sync_timestamp: null,
  },
  {
    service: "lastfm",
    entity_type: "likes",
    has_previous_sync: false,
    last_sync_timestamp: null,
  },
  {
    service: "spotify",
    entity_type: "plays",
    has_previous_sync: false,
    last_sync_timestamp: null,
  },
];

function setupCheckpointsMock() {
  server.use(
    http.get("*/api/v1/imports/checkpoints", () =>
      HttpResponse.json(checkpoints),
    ),
  );
}

describe("Sync page", () => {
  it("renders page header", () => {
    setupCheckpointsMock();
    renderWithProviders(<Sync />);

    expect(screen.getByText("Sync")).toBeInTheDocument();
    expect(
      screen.getByText("Import and sync your music data across services."),
    ).toBeInTheDocument();
  });

  it("renders section headings for data type groups", () => {
    setupCheckpointsMock();
    renderWithProviders(<Sync />);

    expect(screen.getByText("Listening History")).toBeInTheDocument();
    expect(screen.getByText("Liked Tracks")).toBeInTheDocument();
  });

  it("renders all four operation cards", () => {
    setupCheckpointsMock();
    renderWithProviders(<Sync />);

    expect(screen.getByText("Scrobble History")).toBeInTheDocument();
    expect(screen.getByText("Import Likes")).toBeInTheDocument();
    expect(screen.getByText("Export Loves")).toBeInTheDocument();
    expect(screen.getByText("Spotify Data Export")).toBeInTheDocument();
  });

  it("renders connector icons for service identification", () => {
    setupCheckpointsMock();
    renderWithProviders(<Sync />);

    const spotifyIcons = screen.getAllByTitle("Spotify");
    const lastfmIcons = screen.getAllByTitle("Last.fm");

    expect(spotifyIcons).toHaveLength(2);
    expect(lastfmIcons).toHaveLength(2);
  });

  it("renders run buttons for each operation", () => {
    setupCheckpointsMock();
    renderWithProviders(<Sync />);

    const importButtons = screen.getAllByRole("button", { name: "Import" });
    expect(importButtons).toHaveLength(4);
  });

  it("renders segmented mode selector for Last.fm history", () => {
    setupCheckpointsMock();
    renderWithProviders(<Sync />);

    const recentBtn = screen.getByRole("radio", { name: /recent/i });
    const incrementalBtn = screen.getByRole("radio", {
      name: /since last import/i,
    });
    const fullBtn = screen.getByRole("radio", { name: /full/i });

    expect(recentBtn).toHaveAttribute("aria-checked", "true");
    expect(incrementalBtn).toHaveAttribute("aria-checked", "false");
    expect(fullBtn).toHaveAttribute("aria-checked", "false");
  });

  it("renders file upload for Spotify history", () => {
    setupCheckpointsMock();
    renderWithProviders(<Sync />);

    expect(
      screen.getByRole("button", { name: /choose file/i }),
    ).toBeInTheDocument();
  });
});
