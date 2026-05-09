import { renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { useIsMobile } from "#/hooks/useIsMobile";
import { mockMatchMedia } from "#/test/test-utils";

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
});
