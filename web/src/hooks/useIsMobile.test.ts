import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { useIsMobile } from "#/hooks/useIsMobile";
import { mockMatchMedia } from "#/test/test-utils";

/**
 * `matchMedia` stub whose listeners actually fire, so a test can move the
 * viewport after mount. `mockMatchMedia` covers the static cases.
 */
function mockResizableMatchMedia(initialMatches: boolean) {
  const listeners = new Set<(event: MediaQueryListEvent) => void>();
  let matches = initialMatches;

  Object.defineProperty(window, "matchMedia", {
    writable: true,
    configurable: true,
    value: (query: string) => ({
      get matches() {
        return matches;
      },
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: (_type: string, listener: EventListener) =>
        listeners.add(listener as (event: MediaQueryListEvent) => void),
      removeEventListener: (_type: string, listener: EventListener) =>
        listeners.delete(listener as (event: MediaQueryListEvent) => void),
      dispatchEvent: () => false,
    }),
  });

  return {
    setMatches(next: boolean) {
      matches = next;
      for (const listener of listeners) {
        listener({ matches: next } as MediaQueryListEvent);
      }
    },
    listenerCount: () => listeners.size,
  };
}

describe("useIsMobile", () => {
  it("returns true below the lg breakpoint", () => {
    mockMatchMedia(390);
    const { result } = renderHook(() => useIsMobile());
    expect(result.current).toBe(true);
  });

  it("returns false at-or-above the lg breakpoint", () => {
    mockMatchMedia(1280);
    const { result } = renderHook(() => useIsMobile());
    expect(result.current).toBe(false);
  });

  it("treats 1023px as mobile (boundary inclusive)", () => {
    mockMatchMedia(1023);
    const { result } = renderHook(() => useIsMobile());
    expect(result.current).toBe(true);
  });

  it("treats 1024px as desktop (boundary inclusive)", () => {
    mockMatchMedia(1024);
    const { result } = renderHook(() => useIsMobile());
    expect(result.current).toBe(false);
  });

  it("re-renders when the media query changes", () => {
    const mql = mockResizableMatchMedia(false);
    const { result } = renderHook(() => useIsMobile());
    expect(result.current).toBe(false);

    act(() => mql.setMatches(true));
    expect(result.current).toBe(true);

    act(() => mql.setMatches(false));
    expect(result.current).toBe(false);
  });

  it("drops its listener on unmount", () => {
    const mql = mockResizableMatchMedia(false);
    const { unmount } = renderHook(() => useIsMobile());
    expect(mql.listenerCount()).toBe(1);

    unmount();

    expect(mql.listenerCount()).toBe(0);
  });
});
