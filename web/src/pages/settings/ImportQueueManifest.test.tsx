import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ImportQueueEntrySchema } from "#/api/generated/model";

import { ImportQueueManifest } from "./ImportQueueManifest";

function entry(
  overrides: Partial<ImportQueueEntrySchema> = {},
): ImportQueueEntrySchema {
  return {
    filename: "history-2019.json",
    position: 0,
    status: "queued",
    operation_id: null,
    run_id: null,
    size_bytes: 1000,
    ...overrides,
  };
}

function renderManifest(entries: ImportQueueEntrySchema[]) {
  return render(
    <ImportQueueManifest
      entries={entries}
      subOperation={null}
      onCancelRemaining={vi.fn()}
      cancelDisabled={false}
    />,
  );
}

describe("ImportQueueManifest", () => {
  it("labels each file with the shared run-status vocabulary", () => {
    renderManifest([
      entry({ filename: "a.json", position: 0, status: "complete" }),
      entry({ filename: "b.json", position: 1, status: "partial" }),
      entry({ filename: "c.json", position: 2, status: "error" }),
      entry({ filename: "d.json", position: 3, status: "running" }),
      entry({ filename: "e.json", position: 4, status: "cancelled" }),
      entry({ filename: "f.json", position: 5 }),
    ]);

    // Same labels the audit row wears in Import History — a file and the run it
    // becomes must not disagree about what its status is called.
    expect(screen.getByLabelText("Complete")).toBeInTheDocument();
    expect(screen.getByLabelText("Completed with issues")).toBeInTheDocument();
    expect(screen.getByLabelText("Error")).toBeInTheDocument();
    expect(screen.getByLabelText("Running")).toBeInTheDocument();
    expect(screen.getByLabelText("Cancelled")).toBeInTheDocument();
    expect(screen.getByLabelText("Queued")).toBeInTheDocument();
  });

  it("tells a settled file's counts apart from a failed file's reason", () => {
    renderManifest([
      entry({
        filename: "done.json",
        position: 0,
        status: "complete",
        started_at: "2026-08-09T10:00:00Z",
        settled_at: "2026-08-09T10:01:00Z",
        counts: { track_plays: 1200 },
      }),
      entry({
        filename: "broken.json",
        position: 1,
        status: "error",
        counts: { error_message: "Unexpected end of JSON input" },
      }),
    ]);

    // Scoped to the rows: the header sums the same plays.
    const rows = within(screen.getByRole("list"));
    expect(rows.getByText(/1,200 plays/)).toBeInTheDocument();
    expect(
      rows.getByText("Failed — Unexpected end of JSON input"),
    ).toBeInTheDocument();
  });

  it("places a waiting file in the line", () => {
    renderManifest([
      entry({
        filename: "a.json",
        position: 0,
        status: "complete",
        started_at: "2026-08-09T10:00:00Z",
        settled_at: "2026-08-09T10:01:00Z",
      }),
      entry({
        filename: "b.json",
        position: 1,
        status: "complete",
        started_at: "2026-08-09T10:01:00Z",
        settled_at: "2026-08-09T10:02:00Z",
      }),
      entry({
        filename: "c.json",
        position: 2,
        status: "running",
        started_at: "2026-08-09T10:02:00Z",
      }),
      entry({ filename: "d.json", position: 3 }),
    ]);

    expect(
      screen.getByText(/Queued · #1 · starts in ~1 min/),
    ).toBeInTheDocument();
  });
});
