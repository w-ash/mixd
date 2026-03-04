import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import type { CheckpointStatusSchema } from "@/api/generated/model";
import { server } from "@/test/setup";
import { renderWithProviders, screen, waitFor } from "@/test/test-utils";

import { Imports } from "./Imports";

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

describe("Imports page", () => {
  it("renders page header", () => {
    setupCheckpointsMock();
    renderWithProviders(<Imports />);

    expect(screen.getByText("Imports")).toBeInTheDocument();
    expect(
      screen.getByText("Import and sync your music data across services."),
    ).toBeInTheDocument();
  });

  it("renders all four import cards", () => {
    setupCheckpointsMock();
    renderWithProviders(<Imports />);

    expect(screen.getByText("Import Last.fm History")).toBeInTheDocument();
    expect(screen.getByText("Import Spotify Likes")).toBeInTheDocument();
    expect(screen.getByText("Export Likes to Last.fm")).toBeInTheDocument();
    expect(screen.getByText("Import Spotify History")).toBeInTheDocument();
  });

  it("renders run buttons for each operation", () => {
    setupCheckpointsMock();
    renderWithProviders(<Imports />);

    const runButtons = screen.getAllByRole("button", { name: "Run" });
    expect(runButtons).toHaveLength(4);
  });

  it("displays checkpoint data when loaded", async () => {
    setupCheckpointsMock();
    renderWithProviders(<Imports />);

    await waitFor(() => {
      expect(screen.getByText("Sync Status")).toBeInTheDocument();
    });

    await waitFor(() => {
      // The synced checkpoint shows a date, the others show "Never synced"
      const neverSyncedElements = screen.getAllByText("Never synced");
      expect(neverSyncedElements.length).toBeGreaterThanOrEqual(3);
    });
  });

  it("renders mode selector for Last.fm history", () => {
    setupCheckpointsMock();
    renderWithProviders(<Imports />);

    expect(screen.getByLabelText("Mode:")).toBeInTheDocument();
    expect(screen.getByRole("combobox")).toHaveValue("recent");
  });

  it("renders file upload for Spotify history", () => {
    setupCheckpointsMock();
    renderWithProviders(<Imports />);

    expect(
      screen.getByRole("button", { name: /choose file/i }),
    ).toBeInTheDocument();
  });

  it("shows empty state when no checkpoints exist", async () => {
    server.use(
      http.get("*/api/v1/imports/checkpoints", () => HttpResponse.json([])),
    );
    renderWithProviders(<Imports />);

    await waitFor(() => {
      expect(screen.getByText(/no sync history yet/i)).toBeInTheDocument();
    });
  });
});
