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
// completion toast, and there is no live stream in jsdom. Operation ids are
// recorded so tests can assert which id a card attached its stream to.
let mockProgress: OperationProgress | null = null;
const seenOperationIds: (string | null)[] = [];
vi.mock("#/hooks/useOperationProgress", () => ({
  useOperationProgress: (operationId: string | null) => {
    seenOperationIds.push(operationId);
    return {
      progress: mockProgress,
      isActive: false,
      isConnected: false,
      error: null,
    };
  },
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
  seenOperationIds.length = 0;
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

// ─── Spotify GDPR import queue ──────────────────────────────────

type QueueEntry = {
  filename: string;
  position: number;
  status: string;
  operation_id: string | null;
  run_id: string | null;
};

function queueJson(entries: QueueEntry[]) {
  return { queue_id: "queue-1", entries };
}

function useQueueHandler(...states: QueueEntry[][]) {
  // Serve each state once, then repeat the last — the component's poll is the
  // only thing that advances it, so chip movement is provably refetch-driven.
  let call = 0;
  server.use(
    http.get("*/api/v1/imports/spotify/history/queue", () => {
      const state = states[Math.min(call, states.length - 1)];
      call += 1;
      return HttpResponse.json(queueJson(state));
    }),
  );
}

describe("Spotify history import queue", () => {
  it("rebuilds the per-file chip list from the queue endpoint after a reload", async () => {
    // No POST happens in this test: the mounted page finding a mid-drain
    // queue on the server IS the reload re-attach.
    setupCheckpointsMock();
    useQueueHandler([
      {
        filename: "history-2019.json",
        position: 0,
        status: "complete",
        operation_id: "op-a",
        run_id: "run-a",
      },
      {
        filename: "history-2020.json",
        position: 1,
        status: "running",
        operation_id: "op-b",
        run_id: "run-b",
      },
      {
        filename: "history-2021.json",
        position: 2,
        status: "queued",
        operation_id: null,
        run_id: null,
      },
    ]);
    renderWithProviders(<Sync />);

    expect(await screen.findByText("history-2019.json")).toBeInTheDocument();
    expect(screen.getByText("history-2020.json")).toBeInTheDocument();
    expect(screen.getByText("history-2021.json")).toBeInTheDocument();
    expect(screen.getByText("Complete")).toBeInTheDocument();
    expect(screen.getByText("Running")).toBeInTheDocument();
    expect(screen.getByText("Queued")).toBeInTheDocument();

    // The card re-attached its progress stream to the RUNNING entry's id.
    await waitFor(() => {
      expect(seenOperationIds).toContain("op-b");
    });
  });

  it("a terminal event advances the next chip via refetch", async () => {
    setupCheckpointsMock();
    useQueueHandler(
      [
        {
          filename: "a.json",
          position: 0,
          status: "running",
          operation_id: "op-a",
          run_id: "run-a",
        },
        {
          filename: "b.json",
          position: 1,
          status: "queued",
          operation_id: null,
          run_id: null,
        },
      ],
      [
        {
          filename: "a.json",
          position: 0,
          status: "error",
          operation_id: "op-a",
          run_id: "run-a",
        },
        {
          filename: "b.json",
          position: 1,
          status: "running",
          operation_id: "op-b",
          run_id: "run-b",
        },
      ],
    );
    renderWithProviders(<Sync />);

    expect(await screen.findByText("a.json")).toBeInTheDocument();
    expect(screen.getByText("Running")).toBeInTheDocument();
    expect(screen.getByText("Queued")).toBeInTheDocument();

    // The poll picks up the server's new state: the failed file shows its
    // error chip and the queue visibly continues with the next file running.
    await waitFor(
      () => {
        expect(screen.getByText("Error")).toBeInTheDocument();
        expect(screen.getByText("Running")).toBeInTheDocument();
      },
      { timeout: 4000 },
    );
    expect(screen.queryByText("Queued")).not.toBeInTheDocument();
  });

  it("Cancel remaining sends the DELETE for not-yet-started files", async () => {
    setupCheckpointsMock();
    useQueueHandler([
      {
        filename: "a.json",
        position: 0,
        status: "running",
        operation_id: "op-a",
        run_id: "run-a",
      },
      {
        filename: "b.json",
        position: 1,
        status: "queued",
        operation_id: null,
        run_id: null,
      },
    ]);
    const deleteCalls: number[] = [];
    server.use(
      http.delete("*/api/v1/imports/spotify/history/queue", () => {
        deleteCalls.push(1);
        return HttpResponse.json(
          queueJson([
            {
              filename: "a.json",
              position: 0,
              status: "running",
              operation_id: "op-a",
              run_id: "run-a",
            },
            {
              filename: "b.json",
              position: 1,
              status: "cancelled",
              operation_id: null,
              run_id: null,
            },
          ]),
        );
      }),
    );
    renderWithProviders(<Sync />);

    await userEvent.click(
      await screen.findByRole("button", { name: /cancel remaining/i }),
    );

    await waitFor(() => {
      expect(deleteCalls).toHaveLength(1);
    });
  });
});
