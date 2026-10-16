/**
 * Threshold matrix for formatProgressLabel.
 *
 * Pure unit tests — no React, no async, no mocks beyond the input shape.
 * Threshold logic lives in lib/ specifically so it can be regression-tested
 * cheaply when product tweaks the UX rules.
 */

import { describe, expect, it } from "vitest";

import { formatEta, formatProgressLabel, formatRate } from "./eta";

describe("formatProgressLabel", () => {
  // Spec (module header): indeterminate unless total is known and > 0.
  it.each([null, undefined, 0])(
    "falls back to indeterminate copy when total is %s",
    (total) => {
      const result = formatProgressLabel({
        current: 5,
        total,
        message: "Fetching things",
      });
      expect(result.hasEta).toBe(false);
      expect(result.label).toBe("Fetching 5 items…");
    },
  );

  it("shows ETA when rate, eta, and completion are all healthy", () => {
    const result = formatProgressLabel({
      current: 12,
      total: 87,
      message: "Enriching tracks",
      itemsPerSecond: 12.5,
      etaSeconds: 6,
    });
    expect(result.hasEta).toBe(true);
    expect(result.label).toBe("Enriching 12/87 tracks · 13/sec · ETA 6s");
  });

  // Spec (module header): ETA only when rate > 0, completion < 80%, ETA > 3s.
  // Each row sits on the boundary that hides it; everything else is healthy.
  it.each([
    ["the rate is missing", 12, 87, undefined, 6, "Enriching 12/87 tracks…"],
    ["the rate is zero", 12, 87, 0, 6, "Enriching 12/87 tracks…"],
    ["completion reaches 80%", 80, 100, 12, 10, "Enriching 80/100 tracks…"],
    ["the ETA is exactly 3s", 12, 87, 12, 3, "Enriching 12/87 tracks…"],
  ] as const)(
    "hides the ETA when %s",
    (_, current, total, itemsPerSecond, etaSeconds, label) => {
      const result = formatProgressLabel({
        current,
        total,
        message: "Enriching tracks",
        itemsPerSecond,
        etaSeconds,
      });
      expect(result).toEqual({ hasEta: false, label });
    },
  );
});

describe("formatRate", () => {
  it("formats slow rates per minute", () => {
    expect(formatRate(0.5)).toBe("30.0/min");
  });

  it("keeps one decimal from 1/sec up to 10/sec, and none from 10/sec", () => {
    expect(formatRate(1)).toBe("1.0/sec");
    expect(formatRate(2.5)).toBe("2.5/sec");
    expect(formatRate(10)).toBe("10/sec");
    expect(formatRate(13.4)).toBe("13/sec");
  });
});

describe("formatEta", () => {
  it("rounds seconds up and splits minutes", () => {
    expect(formatEta(4.2)).toBe("5s");
    expect(formatEta(125)).toBe("2m 5s");
    expect(formatEta(120)).toBe("2m");
  });

  it("rounds before splitting, so no part overflows", () => {
    expect(formatEta(119.5)).toBe("2m");
    expect(formatEta(59.6)).toBe("1m");
  });
});
