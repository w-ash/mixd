/**
 * Repro for the prod sequence the generated MSW defaults never exercise:
 * the page loads with NO queue (GET 404 → query error state), the user
 * uploads, and the queue section must appear without a reload.
 */
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { server } from "#/test/setup";
import {
  renderWithProviders,
  screen,
  userEvent,
  waitFor,
} from "#/test/test-utils";

import { Sync } from "./Sync";

vi.mock("#/hooks/useOperationProgress", () => ({
  useOperationProgress: () => ({
    progress: null,
    isActive: false,
    isConnected: false,
    error: null,
  }),
}));

describe("Spotify Data Export queue appearance", () => {
  beforeEach(() => {
    let queueExists = false;
    const entries = (status0: string, status1: string) => [
      {
        filename: "Streaming_History_Audio_2014.json",
        position: 0,
        status: status0,
        operation_id: status0 === "running" ? "op-1" : null,
        run_id: status0 === "running" ? "run-1" : null,
      },
      {
        filename: "Streaming_History_Audio_2014_1.json",
        position: 1,
        status: status1,
        operation_id: null,
        run_id: null,
      },
    ];
    server.use(
      http.get("*/api/v1/imports/checkpoints", () => HttpResponse.json([])),
      http.get("*/api/v1/imports/spotify/history/queue", () => {
        if (!queueExists) {
          return HttpResponse.json(
            { detail: "No import queue" },
            { status: 404 },
          );
        }
        return HttpResponse.json({
          queue_id: "q-1",
          entries: entries("running", "queued"),
        });
      }),
      http.post("*/api/v1/imports/spotify/history", () => {
        queueExists = true;
        return HttpResponse.json({
          queue_id: "q-1",
          entries: entries("queued", "queued"),
        });
      }),
    );
  });

  it("shows the queue section after an upload that starts the first-ever queue", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Sync />);

    const input = await screen.findByLabelText(/upload files/i);
    const fileA = new File(["[]"], "Streaming_History_Audio_2014.json", {
      type: "application/json",
    });
    const fileB = new File(["[]"], "Streaming_History_Audio_2014_1.json", {
      type: "application/json",
    });
    await user.upload(input, [fileA, fileB]);

    await user.click(
      await screen.findByRole("button", { name: /import 2 files/i }),
    );

    await waitFor(
      () => {
        expect(screen.getByText("Import queue")).toBeInTheDocument();
      },
      { timeout: 4000 },
    );

    // The file picker resets once the queue exists — stale filenames lingering
    // in the drop zone (FileUpload's own state) was half of the original bug.
    await waitFor(() => {
      expect(
        screen.queryByRole("button", { name: /change/i }),
      ).not.toBeInTheDocument();
    });
  });
});
