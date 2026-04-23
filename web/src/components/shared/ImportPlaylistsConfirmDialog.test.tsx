import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type {
  OperationProgress,
  SubOperationRecord,
} from "#/hooks/useOperationProgress";
import { makeConnectorMetadata } from "#/test/factories";
import { server } from "#/test/setup";
import {
  renderWithProviders,
  screen,
  userEvent,
  waitFor,
} from "#/test/test-utils";

import { ImportPlaylistsConfirmDialog } from "./ImportPlaylistsConfirmDialog";

// Mock useOperationProgress so tests can drive the SSE flow deterministically
// without spinning up an EventSource. Each test wires a progress fixture
// directly and asserts the dialog's rendering + toast side effects.
let mockProgress: OperationProgress | null = null;
vi.mock("#/hooks/useOperationProgress", async () => {
  const actual = await vi.importActual<
    typeof import("#/hooks/useOperationProgress")
  >("#/hooks/useOperationProgress");
  return {
    ...actual,
    useOperationProgress: (operationId: string | null) => ({
      progress: operationId ? mockProgress : null,
      isActive:
        mockProgress?.status === "running" ||
        mockProgress?.status === "pending",
      isConnected: operationId !== null,
      error: null,
    }),
  };
});

const mockToastSuccess = vi.fn();
const mockToastError = vi.fn();
const mockToastInfo = vi.fn();
vi.mock("#/lib/toasts", async () => {
  const actual =
    await vi.importActual<typeof import("#/lib/toasts")>("#/lib/toasts");
  return {
    ...actual,
    toasts: {
      ...actual.toasts,
      success: (...args: unknown[]) => mockToastSuccess(...args),
      message: (...args: unknown[]) => mockToastError(...args),
      error: (...args: unknown[]) => mockToastError(...args),
      info: (...args: unknown[]) => mockToastInfo(...args),
    },
  };
});

const SPOTIFY_CONNECTOR = makeConnectorMetadata({
  name: "spotify",
  connected: true,
  status: "connected",
});

beforeEach(() => {
  mockProgress = null;
  mockToastSuccess.mockReset();
  mockToastError.mockReset();
  mockToastInfo.mockReset();
});

function setup(
  overrides: Partial<Parameters<typeof ImportPlaylistsConfirmDialog>[0]> = {},
) {
  const onOpenChange = vi.fn();
  const onImported = vi.fn();
  const utils = renderWithProviders(
    <ImportPlaylistsConfirmDialog
      open={true}
      connector={SPOTIFY_CONNECTOR}
      onOpenChange={onOpenChange}
      playlists={[
        { id: "sp1", name: "Chill Vibes" },
        { id: "sp2", name: "Workout Mix" },
      ]}
      onImported={onImported}
      {...overrides}
    />,
  );
  return { ...utils, onOpenChange, onImported };
}

function record(
  overrides: Partial<SubOperationRecord> = {},
): SubOperationRecord {
  return {
    operationId: "op-sub",
    connectorPlaylistIdentifier: null,
    playlistName: null,
    outcome: null,
    resolved: null,
    unresolved: null,
    errorMessage: null,
    phase: null,
    canonicalPlaylistId: null,
    ...overrides,
  };
}

function progress(
  overrides: Partial<OperationProgress> = {},
): OperationProgress {
  return {
    status: "completed",
    current: 2,
    total: 2,
    message: "Complete",
    description: "Importing 2 playlists from spotify",
    completionPercentage: 100,
    itemsPerSecond: null,
    etaSeconds: null,
    subOperation: null,
    subOperationHistory: {},
    ...overrides,
  };
}

/** Mock the POST endpoint to return a 202 + operation_id. */
function mockImport202(operationId: string = "op-123") {
  server.use(
    http.post("*/api/v1/connectors/spotify/playlists/import", () =>
      HttpResponse.json({ operation_id: operationId }, { status: 202 }),
    ),
  );
}

describe("ImportPlaylistsConfirmDialog", () => {
  describe("compose phase", () => {
    it("renders the playlist count and selected names", async () => {
      setup();
      expect(
        await screen.findByRole("heading", { name: "Import 2 playlists" }),
      ).toBeInTheDocument();
      expect(screen.getByText("Chill Vibes")).toBeInTheDocument();
      expect(screen.getByText("Workout Mix")).toBeInTheDocument();
    });

    it("defaults to Spotify-managed (pull) direction", async () => {
      setup();
      expect(
        screen.getByRole("radio", { name: /Spotify-managed/ }),
      ).toBeChecked();
    });

    it("truncates the playlist list at 10 with '… and N more'", async () => {
      const many = Array.from({ length: 15 }, (_, i) => ({
        id: `pl${i}`,
        name: `Playlist ${i}`,
      }));
      setup({ playlists: many });
      expect(await screen.findByText("Playlist 0")).toBeInTheDocument();
      expect(screen.getByText("Playlist 9")).toBeInTheDocument();
      expect(screen.queryByText("Playlist 10")).not.toBeInTheDocument();
      expect(screen.getByText("… and 5 more")).toBeInTheDocument();
    });

    it("disables the Import button when the ID list is empty", async () => {
      setup({ playlists: [] });
      expect(
        await screen.findByRole("button", { name: "Import 0 playlists" }),
      ).toBeDisabled();
    });

    it("closes without submitting on cancel", async () => {
      const { onOpenChange } = setup();
      await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
      expect(onOpenChange).toHaveBeenCalledWith(false);
    });

    it("renders the Force re-fetch toggle off by default", async () => {
      setup();
      const toggle = await screen.findByRole("switch", {
        name: "Force re-fetch from connector",
      });
      expect(toggle).toHaveAttribute("aria-checked", "false");
    });

    it("forwards force=true in the import request when toggle is on", async () => {
      const importBody = vi.fn();
      server.use(
        http.post(
          "*/api/v1/connectors/spotify/playlists/import",
          async ({ request }) => {
            importBody(await request.json());
            return HttpResponse.json(
              { operation_id: "op-force" },
              { status: 202 },
            );
          },
        ),
      );

      setup();
      await userEvent.click(
        await screen.findByRole("switch", {
          name: "Force re-fetch from connector",
        }),
      );
      await userEvent.click(
        screen.getByRole("button", { name: "Import 2 playlists" }),
      );

      await waitFor(() => expect(importBody).toHaveBeenCalled());
      expect(importBody.mock.calls[0][0]).toMatchObject({ force: true });
    });

    it("defaults to force=false when the toggle is left off", async () => {
      const importBody = vi.fn();
      server.use(
        http.post(
          "*/api/v1/connectors/spotify/playlists/import",
          async ({ request }) => {
            importBody(await request.json());
            return HttpResponse.json(
              { operation_id: "op-default" },
              { status: 202 },
            );
          },
        ),
      );

      setup();
      await userEvent.click(
        await screen.findByRole("button", { name: "Import 2 playlists" }),
      );

      await waitFor(() => expect(importBody).toHaveBeenCalled());
      expect(importBody.mock.calls[0][0]).toMatchObject({ force: false });
    });
  });

  describe("terminal toast — outcome matrix", () => {
    it("all succeeded → success toast + onImported", async () => {
      mockImport202();
      const { onImported, rerender } = setup();

      // Submit → mutation fires, onSuccess sets operationId.
      await userEvent.click(
        screen.getByRole("button", { name: "Import 2 playlists" }),
      );

      // Now drive the SSE stream by setting the mocked progress to a
      // terminal success state with two succeeded sub-ops, then re-render
      // so the hook's memoized value flushes.
      mockProgress = progress({
        subOperationHistory: {
          sp1: record({
            connectorPlaylistIdentifier: "sp1",
            playlistName: "Chill Vibes",
            outcome: "succeeded",
            resolved: 10,
            unresolved: 0,
          }),
          sp2: record({
            connectorPlaylistIdentifier: "sp2",
            playlistName: "Workout Mix",
            outcome: "succeeded",
            resolved: 8,
            unresolved: 2,
          }),
        },
      });
      rerender(
        <ImportPlaylistsConfirmDialog
          open={true}
          connector={SPOTIFY_CONNECTOR}
          onOpenChange={vi.fn()}
          playlists={[
            { id: "sp1", name: "Chill Vibes" },
            { id: "sp2", name: "Workout Mix" },
          ]}
          onImported={onImported}
        />,
      );

      await waitFor(() => {
        expect(mockToastSuccess).toHaveBeenCalledOnce();
      });
      expect(mockToastSuccess.mock.calls[0]?.[0]).toContain("Imported 2");
      expect(onImported).toHaveBeenCalledOnce();
    });

    it("all already linked → info toast (not success)", async () => {
      mockImport202();
      const { onImported, rerender } = setup();
      await userEvent.click(
        screen.getByRole("button", { name: "Import 2 playlists" }),
      );

      mockProgress = progress({
        subOperationHistory: {
          sp1: record({
            connectorPlaylistIdentifier: "sp1",
            playlistName: "Chill Vibes",
            outcome: "skipped_unchanged",
          }),
          sp2: record({
            connectorPlaylistIdentifier: "sp2",
            playlistName: "Workout Mix",
            outcome: "skipped_unchanged",
          }),
        },
      });
      rerender(
        <ImportPlaylistsConfirmDialog
          open={true}
          connector={SPOTIFY_CONNECTOR}
          onOpenChange={vi.fn()}
          playlists={[
            { id: "sp1", name: "Chill Vibes" },
            { id: "sp2", name: "Workout Mix" },
          ]}
          onImported={onImported}
        />,
      );

      await waitFor(() => {
        expect(mockToastInfo).toHaveBeenCalledOnce();
      });
      expect(mockToastInfo.mock.calls[0]?.[0]).toContain("already up to date");
      expect(mockToastSuccess).not.toHaveBeenCalled();
    });

    it("any failure → error toast with failure messages (even on partial success)", async () => {
      mockImport202();
      const { onImported, rerender } = setup();
      await userEvent.click(
        screen.getByRole("button", { name: "Import 2 playlists" }),
      );

      mockProgress = progress({
        status: "completed",
        subOperationHistory: {
          sp1: record({
            connectorPlaylistIdentifier: "sp1",
            playlistName: "Chill Vibes",
            outcome: "succeeded",
            resolved: 10,
          }),
          sp2: record({
            connectorPlaylistIdentifier: "sp2",
            playlistName: "Workout Mix",
            outcome: "failed",
            errorMessage: "404 Not Found",
            phase: "fetch",
          }),
        },
      });
      rerender(
        <ImportPlaylistsConfirmDialog
          open={true}
          connector={SPOTIFY_CONNECTOR}
          onOpenChange={vi.fn()}
          playlists={[
            { id: "sp1", name: "Chill Vibes" },
            { id: "sp2", name: "Workout Mix" },
          ]}
          onImported={onImported}
        />,
      );

      await waitFor(() => {
        expect(mockToastError).toHaveBeenCalledOnce();
      });
      const firstCall = mockToastError.mock.calls[0];
      expect(firstCall?.[0]).toContain("Import had errors");
      const description = (firstCall?.[1] as { description?: string })
        ?.description;
      expect(description).toContain("Workout Mix");
      expect(description).toContain("404 Not Found");
      expect(mockToastSuccess).not.toHaveBeenCalled();
    });

    it("empty response (no outcomes) → no toast", async () => {
      mockImport202();
      const { rerender } = setup();
      await userEvent.click(
        screen.getByRole("button", { name: "Import 2 playlists" }),
      );

      mockProgress = progress({ subOperationHistory: {} });
      rerender(
        <ImportPlaylistsConfirmDialog
          open={true}
          connector={SPOTIFY_CONNECTOR}
          onOpenChange={vi.fn()}
          playlists={[
            { id: "sp1", name: "Chill Vibes" },
            { id: "sp2", name: "Workout Mix" },
          ]}
          onImported={vi.fn()}
        />,
      );

      // Give the effect a tick; none of the three toasts should fire.
      await new Promise((resolve) => setTimeout(resolve, 10));
      expect(mockToastSuccess).not.toHaveBeenCalled();
      expect(mockToastError).not.toHaveBeenCalled();
      expect(mockToastInfo).not.toHaveBeenCalled();
    });
  });

  describe("running phase", () => {
    it("renders per-playlist rows with pending indicators before terminal", async () => {
      mockImport202();
      const { rerender } = setup();
      await userEvent.click(
        screen.getByRole("button", { name: "Import 2 playlists" }),
      );

      mockProgress = progress({
        status: "running",
        message: "Fetching 'Chill Vibes' — 50/100 tracks",
        current: 0,
        total: 2,
        subOperation: {
          operationId: "sub-1",
          description: "Chill Vibes",
          current: 50,
          total: 100,
          message: "Fetching 'Chill Vibes' from spotify — 50/100 tracks",
          phase: "fetch",
          completionPercentage: 50,
          connectorPlaylistIdentifier: "sp1",
          playlistName: "Chill Vibes",
        },
        subOperationHistory: {
          sp1: record({
            connectorPlaylistIdentifier: "sp1",
            playlistName: "Chill Vibes",
            phase: "fetch",
          }),
          // sp2 not started yet — still in initial pending state
          sp2: record({
            connectorPlaylistIdentifier: "sp2",
            playlistName: "Workout Mix",
          }),
        },
      });
      rerender(
        <ImportPlaylistsConfirmDialog
          open={true}
          connector={SPOTIFY_CONNECTOR}
          onOpenChange={vi.fn()}
          playlists={[
            { id: "sp1", name: "Chill Vibes" },
            { id: "sp2", name: "Workout Mix" },
          ]}
          onImported={vi.fn()}
        />,
      );

      // The active sub-op message renders.
      await waitFor(() => {
        expect(
          screen.getByText(/Fetching 'Chill Vibes' from spotify/),
        ).toBeInTheDocument();
      });
      // Pending rows show their fallback name.
      expect(screen.getAllByText("Workout Mix").length).toBeGreaterThan(0);
      // No final toast yet.
      expect(mockToastSuccess).not.toHaveBeenCalled();
      expect(mockToastError).not.toHaveBeenCalled();
      expect(mockToastInfo).not.toHaveBeenCalled();
    });
  });
});
