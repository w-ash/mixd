import { beforeEach, describe, expect, it, vi } from "vitest";

import { toasts } from "./toasts";

// Mock sonner so we can assert the rendered title (which encodes primaryCount).
vi.mock("sonner", () => ({
  toast: Object.assign(vi.fn(), {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
    promise: vi.fn(),
  }),
}));

import { toast } from "sonner";

const noop = () => {};

/**
 * Pins `primaryCount`'s reconciled key map to the REAL backend `summary_metrics`
 * names. These were all dead (no producer) before v0.8.5 — a future backend rename
 * must break a test here, not silently degrade every toast to "… complete".
 */
describe("toasts.runCompleted — primaryCount key reconciliation", () => {
  beforeEach(() => vi.clearAllMocks());

  it.each([
    ["import_lastfm_history", { track_plays: 7 }, "Imported 7 scrobbles"],
    ["import_spotify_history", { track_plays: 3 }, "Imported 3 scrobbles"],
    ["import_spotify_likes", { imported: 5 }, "Imported 5 likes"],
    ["export_lastfm_likes", { exported: 2 }, "Exported 2 loves"],
  ] as const)("%s reads its real metric key → %s", (operationType, counts, expectedTitle) => {
    toasts.runCompleted({
      operationType,
      counts,
      issueCount: 0,
      runId: null,
      onNavigate: noop,
    });
    expect(toast.success).toHaveBeenCalledWith(
      expectedTitle,
      expect.anything(),
    );
  });

  it("singularizes a count of 1", () => {
    toasts.runCompleted({
      operationType: "import_lastfm_history",
      counts: { track_plays: 1 },
      issueCount: 0,
      runId: null,
      onNavigate: noop,
    });
    expect(toast.success).toHaveBeenCalledWith(
      "Imported 1 scrobble",
      expect.anything(),
    );
  });

  it("falls back to the generic title when no known key is present", () => {
    toasts.runCompleted({
      operationType: "import_spotify_likes",
      counts: { not_a_real_key: 99 },
      issueCount: 0,
      runId: null,
      onNavigate: noop,
    });
    expect(toast.success).toHaveBeenCalledWith(
      "Import complete",
      expect.anything(),
    );
  });

  it("falls back to 0/generic title on an empty counts payload", () => {
    toasts.runCompleted({
      operationType: "export_lastfm_likes",
      counts: {},
      issueCount: 0,
      runId: null,
      onNavigate: noop,
    });
    expect(toast.success).toHaveBeenCalledWith(
      "Export complete",
      expect.anything(),
    );
  });

  it("uses a generic title for an operation type with no dedicated title", () => {
    toasts.runCompleted({
      operationType: "some_future_operation",
      counts: {},
      issueCount: 0,
      runId: "run-1",
      failed: true,
      onNavigate: noop,
    });
    expect(toast.error).toHaveBeenCalledWith(
      "Operation failed",
      expect.anything(),
    );
  });

  it("uses the supplied action override instead of the default View log", () => {
    const onRetry = vi.fn();
    toasts.runCompleted({
      operationType: "import_connector_playlists",
      counts: {},
      issueCount: 2,
      runId: "run-1",
      failed: true,
      onNavigate: noop,
      action: { label: "Retry failed only", onClick: onRetry },
    });
    expect(toast.error).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({
        action: expect.objectContaining({ label: "Retry failed only" }),
      }),
    );
  });
});
