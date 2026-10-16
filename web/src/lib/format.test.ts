import { describe, expect, it } from "vitest";

import { formatMetricHeader, formatMetricValue } from "./format";

describe("formatMetricHeader", () => {
  it("converts snake_case to Title Case", () => {
    expect(formatMetricHeader("lastfm_user_playcount")).toBe(
      "Lastfm User Playcount",
    );
  });

  it("handles empty string", () => {
    expect(formatMetricHeader("")).toBe("");
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
    // The suite runs under en-US, as the page tests' "1,234" also assume.
    expect(formatMetricValue(1234)).toBe("1,234");
  });

  it("handles zero (falsy number)", () => {
    expect(formatMetricValue(0)).toBe("0");
  });

  it("converts non-number values to string", () => {
    expect(formatMetricValue("high")).toBe("high");
  });
});
