import { describe, expect, it } from "vitest";

import type { PlayEventSchema } from "#/api/generated/model";
import { bucketRange, formatDayLabel, groupPlaysByDay } from "./play-feed";

// Local-time constructions keep the local-day grouping TZ-independent.
const NOW = new Date(2026, 7, 20, 18, 0);
const localIso = (day: number, hour: number, minute = 0) =>
  new Date(2026, 7, day, hour, minute).toISOString();

function makeEvent(overrides: Partial<PlayEventSchema>): PlayEventSchema {
  return {
    id: crypto.randomUUID(),
    track_id: "track-1",
    title: "Song",
    artists: "Artist",
    played_at: NOW.toISOString(),
    ms_played: null,
    service: "spotify",
    source_services: ["spotify"],
    ...overrides,
  };
}

describe("groupPlaysByDay", () => {
  it("labels today and yesterday, absolute beyond", () => {
    const events = [
      makeEvent({ played_at: localIso(20, 15) }),
      makeEvent({ track_id: "t2", played_at: localIso(19, 10) }),
      makeEvent({ track_id: "t3", played_at: localIso(1, 10) }),
    ];
    const groups = groupPlaysByDay(events, NOW);
    expect(groups.map((g) => g.label)).toEqual([
      "Today",
      "Yesterday",
      formatDayLabel("2026-08-01", NOW),
    ]);
    expect(groups[2].label).toMatch(/Aug/);
  });

  it("collapses consecutive same-track runs with count and range", () => {
    const events = [
      makeEvent({ track_id: "a", played_at: localIso(20, 15, 30) }),
      makeEvent({ track_id: "a", played_at: localIso(20, 15, 20) }),
      makeEvent({ track_id: "a", played_at: localIso(20, 15, 10) }),
      makeEvent({ track_id: "b", played_at: localIso(20, 14) }),
      makeEvent({ track_id: "a", played_at: localIso(20, 13) }),
    ];
    const [today] = groupPlaysByDay(events, NOW);
    expect(today.rows.map((r) => [r.trackId, r.count])).toEqual([
      ["a", 3],
      ["b", 1],
      ["a", 1], // non-consecutive run stays separate
    ]);
    expect(today.rows[0].newestAt).toBe(localIso(20, 15, 30));
    expect(today.rows[0].oldestAt).toBe(localIso(20, 15, 10));
  });

  it("does not collapse a run across a day boundary", () => {
    const events = [
      makeEvent({ track_id: "a", played_at: localIso(20, 12) }),
      makeEvent({ track_id: "a", played_at: localIso(19, 12) }),
    ];
    const groups = groupPlaysByDay(events, NOW);
    expect(groups).toHaveLength(2);
    expect(groups.every((g) => g.rows.length === 1)).toBe(true);
  });
});

describe("bucketRange", () => {
  it("spans one day / week / month from the bin start", () => {
    const start = "2026-08-01T00:00:00.000Z";
    expect(bucketRange(start, "day").to).toBe("2026-08-02T00:00:00.000Z");
    expect(bucketRange(start, "week").to).toBe("2026-08-08T00:00:00.000Z");
    expect(bucketRange(start, "month").to).toBe("2026-09-01T00:00:00.000Z");
    expect(bucketRange(start, "day").from).toBe(start);
  });
});
