import { describe, expect, it } from "vitest";

import { formatMetricHeader, formatMetricValue } from "./format";

describe("formatMetricHeader", () => {
  it("converts snake_case to Title Case", () => {
    expect(formatMetricHeader("lastfm_user_playcount")).toBe(
      "Lastfm User Playcount",
    );
  });

  it("handles single word", () => {
    expect(formatMetricHeader("popularity")).toBe("Popularity");
  });

  it("handles empty string", () => {
    expect(formatMetricHeader("")).toBe("");
  });

  it("preserves already capitalized segments", () => {
    expect(formatMetricHeader("spotify_popularity")).toBe("Spotify Popularity");
  });
});

describe("formatMetricValue", () => {
  it("returns em-dash for null", () => {
    expect(formatMetricValue(null)).toBe("\u2014");
  });

  it("returns em-dash for undefined", () => {
    expect(formatMetricValue(undefined)).toBe("\u2014");
  });

  it("formats numbers with locale separators", () => {
    expect(formatMetricValue(1234)).toBe((1234).toLocaleString());
  });

  it("handles zero (falsy number)", () => {
    expect(formatMetricValue(0)).toBe("0");
  });

  it("converts non-number values to string", () => {
    expect(formatMetricValue("high")).toBe("high");
  });
});
