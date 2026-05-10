/**
 * Threshold matrix for formatProgressLabel.
 *
 * Pure unit tests — no React, no async, no mocks beyond the input shape.
 * Threshold logic lives in lib/ specifically so it can be regression-tested
 * cheaply when product tweaks the UX rules.
 */

import { describe, expect, it } from "vitest";

import { formatProgressLabel } from "./eta";

describe("formatProgressLabel", () => {
  it("falls back to indeterminate copy when total is null", () => {
    const result = formatProgressLabel({
      current: 5,
      total: null,
      message: "Fetching things",
    });
    expect(result.hasEta).toBe(false);
    expect(result.label).toBe("Fetching 5 items…");
  });

  it("shows base count without ETA when itemsPerSecond is missing", () => {
    const result = formatProgressLabel({
      current: 12,
      total: 87,
      message: "Enriching tracks",
      etaSeconds: 6,
    });
    expect(result.hasEta).toBe(false);
    expect(result.label).toBe("Enriching 12/87 tracks…");
  });

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

  it("hides ETA when completion >= 80%", () => {
    const result = formatProgressLabel({
      current: 80,
      total: 87,
      message: "Enriching tracks",
      itemsPerSecond: 12,
      etaSeconds: 1,
    });
    expect(result.hasEta).toBe(false);
  });

  it("hides ETA when eta_seconds <= 3 (don't show flickery sub-3s ETAs)", () => {
    const result = formatProgressLabel({
      current: 12,
      total: 87,
      message: "Enriching tracks",
      itemsPerSecond: 12,
      etaSeconds: 2,
    });
    expect(result.hasEta).toBe(false);
  });

  it("rounds rate to integer when items_per_second >= 10", () => {
    const result = formatProgressLabel({
      current: 12,
      total: 87,
      message: "Enriching tracks",
      itemsPerSecond: 12.7,
      etaSeconds: 6,
    });
    expect(result.label).toContain("13/sec");
  });

  it("shows one decimal when rate is < 10/sec", () => {
    const result = formatProgressLabel({
      current: 12,
      total: 87,
      message: "Enriching tracks",
      itemsPerSecond: 4.2,
      etaSeconds: 18,
    });
    expect(result.label).toContain("4.2/sec");
  });
});
