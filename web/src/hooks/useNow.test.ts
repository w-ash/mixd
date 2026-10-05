import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useNow } from "./useNow";

/** Drive `document.visibilityState`, which jsdom exposes read-only. */
function setVisibility(state: DocumentVisibilityState) {
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    get: () => state,
  });
  document.dispatchEvent(new Event("visibilitychange"));
}

describe("useNow", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-01-01T00:00:00Z"));
  });

  afterEach(() => {
    vi.useRealTimers();
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      get: () => "visible",
    });
  });

  it("advances on every interval", () => {
    const { result } = renderHook(() => useNow(1000));
    const start = result.current;

    act(() => void vi.advanceTimersByTime(3000));

    expect(result.current).toBe(start + 3000);
  });

  it.each([0, -1000])("schedules nothing for an interval of %i", (interval) => {
    const { result } = renderHook(() => useNow(interval));
    const start = result.current;

    act(() => void vi.advanceTimersByTime(60_000));

    expect(result.current).toBe(start);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("pauses while the tab is hidden and resumes with a fresh value", () => {
    const { result } = renderHook(() => useNow(1000));
    const start = result.current;

    act(() => setVisibility("hidden"));
    act(() => void vi.advanceTimersByTime(5000));
    expect(result.current).toBe(start);
    expect(vi.getTimerCount()).toBe(0);

    act(() => setVisibility("visible"));
    // The value catches up immediately rather than after another full tick.
    expect(result.current).toBe(start + 5000);
  });

  it("stops ticking after unmount", () => {
    const { unmount } = renderHook(() => useNow(1000));

    unmount();

    expect(vi.getTimerCount()).toBe(0);
  });
});
