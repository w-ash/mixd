import { describe, expect, it } from "vitest";

import type { ImportQueueEntrySchema } from "#/api/generated/model";
import { orderedEntries, placementOf, summarizeQueue } from "./import-queue";

function entry(
  overrides: Partial<ImportQueueEntrySchema> & { position: number },
): ImportQueueEntrySchema {
  return {
    filename: `file-${overrides.position}.json`,
    status: "queued",
    operation_id: null,
    run_id: null,
    size_bytes: 1000,
    started_at: null,
    settled_at: null,
    counts: null,
    ...overrides,
  };
}

/** A file that ran for `minutes` and imported `plays`. */
function settled(
  position: number,
  startMinute: number,
  minutes: number,
  plays: number,
  status: ImportQueueEntrySchema["status"] = "complete",
): ImportQueueEntrySchema {
  const at = (m: number) => new Date(Date.UTC(2026, 7, 9, 10, m)).toISOString();
  return entry({
    position,
    status,
    started_at: at(startMinute),
    settled_at: at(startMinute + minutes),
    counts: { track_plays: plays },
  });
}

describe("summarizeQueue", () => {
  it("counts progress in finished files, never blending the running one", () => {
    // The property the batch bar rests on: it moves only when a file settles,
    // so it cannot slide backwards at a handover.
    const summary = summarizeQueue([
      settled(0, 0, 1, 100),
      entry({ position: 1, status: "running" }),
      entry({ position: 2 }),
      entry({ position: 3 }),
    ]);

    expect(summary.settled).toBe(1);
    expect(summary.percent).toBe(25);
    expect(summary.running).toBe(1);
    expect(summary.queued).toBe(2);
  });

  it("treats a failed file as settled and keeps the count moving", () => {
    // A bad file does not stop the export, so it must not stall its progress
    // either — otherwise the bar freezes on the one thing that already resolved.
    const before = summarizeQueue([
      entry({ position: 0, status: "running" }),
      entry({ position: 1 }),
    ]);
    const after = summarizeQueue([
      settled(0, 0, 1, 0, "error"),
      entry({ position: 1, status: "running" }),
    ]);

    expect(after.percent).toBeGreaterThan(before.percent);
    expect(after.failed).toBe(1);
  });

  it("sums plays across settled files", () => {
    const summary = summarizeQueue([
      settled(0, 0, 1, 1200),
      settled(1, 1, 1, 800),
      entry({ position: 2, status: "running" }),
    ]);
    expect(summary.plays).toBe(2000);
  });

  it("withholds an estimate until two files have actually settled", () => {
    // One sample says nothing about variance, and a confidently wrong estimate
    // discredits every other number on the panel.
    const one = summarizeQueue([
      settled(0, 0, 1, 100),
      entry({ position: 1, status: "running" }),
    ]);
    expect(one.etaSeconds).toBeNull();

    const two = summarizeQueue([
      settled(0, 0, 1, 100),
      settled(1, 1, 1, 100),
      entry({ position: 2, status: "running" }),
    ]);
    expect(two.etaSeconds).toBe(60);
  });

  it("weights the estimate by bytes, not by file count", () => {
    // Two 1000-byte files took a minute each; a 3000-byte file left should read
    // as three minutes, not one.
    const summary = summarizeQueue([
      settled(0, 0, 1, 100),
      settled(1, 1, 1, 100),
      entry({ position: 2, status: "running", size_bytes: 3000 }),
    ]);
    expect(summary.etaSeconds).toBe(180);
  });

  it("has no estimate for a drained queue", () => {
    const summary = summarizeQueue([
      settled(0, 0, 1, 10),
      settled(1, 1, 1, 10),
    ]);
    expect(summary.etaSeconds).toBeNull();
    expect(summary.percent).toBe(100);
  });
});

describe("orderedEntries", () => {
  it("pins failures to the top, then keeps upload order", () => {
    // The one row that may need a person must not be twenty minutes of
    // scrolling away by the time the export finishes.
    const ordered = orderedEntries([
      entry({ position: 0, status: "complete" }),
      entry({ position: 1, status: "error" }),
      entry({ position: 2, status: "running" }),
      entry({ position: 3 }),
    ]);
    expect(ordered.map((e) => e.position)).toEqual([1, 0, 2, 3]);
  });
});

describe("placementOf", () => {
  const queue = [
    settled(0, 0, 1, 100),
    settled(1, 1, 1, 100),
    entry({ position: 2, status: "running" }),
    entry({ position: 3 }),
    entry({ position: 4 }),
  ];

  it("numbers waiting files from one and estimates their turn", () => {
    expect(placementOf(queue, queue[3])).toEqual({
      place: 1,
      startsInSeconds: 60,
    });
    expect(placementOf(queue, queue[4])).toEqual({
      place: 2,
      startsInSeconds: 120,
    });
  });

  it("is null for anything not waiting", () => {
    expect(placementOf(queue, queue[0])).toBeNull();
    expect(placementOf(queue, queue[2])).toBeNull();
  });

  it("still gives a place when the rate is unknowable", () => {
    const fresh = [
      entry({ position: 0, status: "running" }),
      entry({ position: 1 }),
    ];
    expect(placementOf(fresh, fresh[1])).toEqual({
      place: 1,
      startsInSeconds: null,
    });
  });
});
