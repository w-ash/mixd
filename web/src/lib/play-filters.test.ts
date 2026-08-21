import { describe, expect, it } from "vitest";

import {
  countPlayFilters,
  findRecencyPreset,
  RECENCY_PRESETS,
  recencyLabel,
} from "./play-filters";

describe("recency presets", () => {
  // The dropdown renders `preset.label`; the chip renders `recencyLabel()`.
  // They used to be written out separately in two files, so a preset could
  // be renamed in one and not the other. This pins them to one source.
  it.each(RECENCY_PRESETS)("chip and dropdown agree for $value", (preset) => {
    const days = preset.playedWithin ?? preset.notPlayedWithin;
    expect(days).not.toBeNull();
    expect(recencyLabel(String(days), preset.notPlayedWithin !== null)).toBe(
      preset.label,
    );
  });

  it("every preset carries both bounds so callers never narrow", () => {
    for (const preset of RECENCY_PRESETS) {
      expect(preset).toHaveProperty("playedWithin");
      expect(preset).toHaveProperty("notPlayedWithin");
      // Exactly one direction per preset.
      expect(
        (preset.playedWithin === null) !== (preset.notPlayedWithin === null),
      ).toBe(true);
    }
  });

  it("round-trips a preset through its bounds", () => {
    const preset = RECENCY_PRESETS[0];
    expect(
      findRecencyPreset(preset.playedWithin, preset.notPlayedWithin)?.value,
    ).toBe(preset.value);
  });

  it("has no preset for an unmatched pair", () => {
    expect(findRecencyPreset(null, null)).toBeUndefined();
    expect(findRecencyPreset(999, null)).toBeUndefined();
  });

  it("falls back to a day count for a hand-edited URL value", () => {
    expect(recencyLabel("42", false)).toBe("Played in last 42 days");
    expect(recencyLabel("42", true)).toBe("Not played in 42 days");
  });
});

describe("countPlayFilters", () => {
  const none = {
    minPlays: null,
    neverPlayed: false,
    playedWithin: null,
    notPlayedWithin: null,
  };

  it("is 0 when nothing is set", () => {
    expect(countPlayFilters(none)).toBe(0);
  });

  it("counts play count and recency as one group each", () => {
    expect(countPlayFilters({ ...none, minPlays: "10" })).toBe(1);
    expect(countPlayFilters({ ...none, neverPlayed: true })).toBe(1);
    expect(countPlayFilters({ ...none, playedWithin: "7" })).toBe(1);
    expect(
      countPlayFilters({ ...none, minPlays: "10", playedWithin: "7" }),
    ).toBe(2);
  });

  it("does not double-count two settings of the same control", () => {
    expect(
      countPlayFilters({ ...none, minPlays: "10", neverPlayed: true }),
    ).toBe(1);
  });
});
