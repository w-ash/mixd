import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { CheckpointStatusSchema } from "#/api/generated/model";
import type { OperationProgress } from "#/hooks/useOperationProgress";
import { __resetRunToastLedger } from "#/lib/operation-toast-ledger";
import { makeConnectorMetadata } from "#/test/factories";
import { server } from "#/test/setup";
import {
  renderWithProviders,
  screen,
  userEvent,
  waitFor,
  within,
} from "#/test/test-utils";

import { Sync } from "./Sync";

// Drive the SSE stream deterministically: the terminal event is what fires the
// completion toast, and there is no live stream in jsdom.
let mockProgress: OperationProgress | null = null;
vi.mock("#/hooks/useOperationProgress", () => ({
  useOperationProgress: () => ({
    progress: mockProgress,
    isActive: false,
    isConnected: false,
    error: null,
  }),
}));

const mockRunCompleted = vi.fn();
vi.mock("#/lib/toasts", async () => {
  const actual =
    await vi.importActual<typeof import("#/lib/toasts")>("#/lib/toasts");
  return {
    ...actual,
    toasts: {
      ...actual.toasts,
      runCompleted: (...a: unknown[]) => mockRunCompleted(...a),
    },
  };
});

beforeEach(() => {
  mockProgress = null;
  mockRunCompleted.mockReset();
  __resetRunToastLedger();
});

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

  it("renders all five operation cards", () => {
    setupCheckpointsMock();
    renderWithProviders(<Sync />);

    expect(screen.getByText("Scrobble History")).toBeInTheDocument();
    expect(screen.getByText("Import Likes")).toBeInTheDocument();
    expect(screen.getByText("Export Loves")).toBeInTheDocument();
    expect(screen.getByText("Spotify Recent Plays")).toBeInTheDocument();
    expect(screen.getByText("Spotify Data Export")).toBeInTheDocument();
  });

  it("renders connector icons for service identification", () => {
    setupCheckpointsMock();
    renderWithProviders(<Sync />);

    const spotifyIcons = screen.getAllByTitle("Spotify");
    const lastfmIcons = screen.getAllByTitle("Last.fm");

    expect(spotifyIcons).toHaveLength(3);
    expect(lastfmIcons).toHaveLength(2);
  });

  it("renders run buttons for each operation", () => {
    setupCheckpointsMock();
    renderWithProviders(<Sync />);

    const importButtons = screen.getAllByRole("button", { name: "Import" });
    expect(importButtons).toHaveLength(4);
    const exportButtons = screen.getAllByRole("button", { name: "Export" });
    expect(exportButtons).toHaveLength(1);
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

  it("gates Recent Plays on the listening-history scope", async () => {
    // scope_missing keeps connected=true — likes and playlists still work —
    // so only this card may disable itself.
    setupCheckpointsMock();
    server.use(
      http.get("*/api/v1/connectors", () =>
        HttpResponse.json([
          makeConnectorMetadata({
            name: "spotify",
            connected: true,
            auth_error: "scope_missing",
          }),
        ]),
      ),
    );
    renderWithProviders(<Sync />);

    expect(
      await screen.findByText(
        /Re-connect Spotify in Integrations to grant listening-history access/i,
      ),
    ).toBeInTheDocument();

    const recentCard = screen
      .getByText("Spotify Recent Plays")
      .closest("div.rounded-xl") as HTMLElement;
    expect(
      within(recentCard).getByRole("button", { name: "Import" }),
    ).toBeDisabled();

    // The sibling Spotify Likes import needs no new scope and stays enabled —
    // a scope gap must not read as "Spotify is broken".
    const likesCard = screen
      .getByText("Import Likes")
      .closest("div.rounded-xl") as HTMLElement;
    expect(
      within(likesCard).getByRole("button", { name: "Import" }),
    ).toBeEnabled();
  });

  it("toasts a foreground partial run as completed-with-issues, not success", async () => {
    // The live stream reports a partial run as `completed` (the backend maps it
    // onto the complete event) with the failure count in `counts.errors`. If the
    // card ignores that count, a run that lost tracks gets a bare green toast —
    // strictly less informative than the red one it replaced.
    setupCheckpointsMock();
    server.use(
      http.post("*/api/v1/imports/lastfm/history", () =>
        HttpResponse.json({ operation_id: "op-1", run_id: "run-1" }),
      ),
    );
    mockProgress = {
      status: "completed",
      current: 100,
      total: 100,
      message: "Complete",
      description: null,
      completionPercentage: 100,
      itemsPerSecond: null,
      etaSeconds: null,
      counts: { track_plays: 98, errors: 2 },
      subOperation: null,
      subOperationHistory: {},
    };
    renderWithProviders(<Sync />);

    const historyCard = screen
      .getByText("Scrobble History")
      .closest("div.rounded-xl") as HTMLElement;
    await userEvent.click(
      within(historyCard).getByRole("button", { name: "Import" }),
    );

    await waitFor(() => {
      expect(mockRunCompleted).toHaveBeenCalledWith(
        expect.objectContaining({
          operationType: "import_lastfm_history",
          issueCount: 2,
          failed: false,
          runId: "run-1",
        }),
      );
    });
  });

  it("does not double-announce a run the global watcher already claimed", async () => {
    setupCheckpointsMock();
    server.use(
      http.post("*/api/v1/imports/lastfm/history", () =>
        HttpResponse.json({ operation_id: "op-2", run_id: "run-2" }),
      ),
    );
    __resetRunToastLedger();
    // Simulate the watcher winning the race for this run's toast.
    const { claimRunToast } = await import("#/lib/operation-toast-ledger");
    claimRunToast("run-2");
    mockProgress = {
      status: "completed",
      current: 1,
      total: 1,
      message: "Complete",
      description: null,
      completionPercentage: 100,
      itemsPerSecond: null,
      etaSeconds: null,
      counts: { track_plays: 1 },
      subOperation: null,
      subOperationHistory: {},
    };
    renderWithProviders(<Sync />);

    const historyCard = screen
      .getByText("Scrobble History")
      .closest("div.rounded-xl") as HTMLElement;
    await userEvent.click(
      within(historyCard).getByRole("button", { name: "Import" }),
    );

    await new Promise((r) => setTimeout(r, 20));
    expect(mockRunCompleted).not.toHaveBeenCalled();
  });
});
