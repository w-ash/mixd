import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { useAnimatedPresence } from "./useAnimatedPresence";

describe("useAnimatedPresence", () => {
  it("shouldRender is true when isOpen is true", () => {
    const { result } = renderHook(() => useAnimatedPresence(true));

    expect(result.current.shouldRender).toBe(true);
  });

  it("shouldRender starts false when isOpen is false", () => {
    const { result } = renderHook(() => useAnimatedPresence(false));

    expect(result.current.shouldRender).toBe(false);
  });

  it("state is 'open' when isOpen is true", () => {
    const { result } = renderHook(() => useAnimatedPresence(true));

    expect(result.current.state).toBe("open");
  });

  it("state is 'closed' when isOpen starts false", () => {
    const { result } = renderHook(() => useAnimatedPresence(false));

    expect(result.current.state).toBe("closed");
  });

  it("state transitions to 'open' when isOpen changes to true", () => {
    const { result, rerender } = renderHook(
      ({ isOpen }) => useAnimatedPresence(isOpen),
      { initialProps: { isOpen: false } },
    );

    expect(result.current.state).toBe("closed");
    expect(result.current.shouldRender).toBe(false);

    rerender({ isOpen: true });

    expect(result.current.state).toBe("open");
    expect(result.current.shouldRender).toBe(true);
  });

  it("state transitions to 'closed' when isOpen changes to false", () => {
    const { result, rerender } = renderHook(
      ({ isOpen }) => useAnimatedPresence(isOpen),
      { initialProps: { isOpen: true } },
    );

    expect(result.current.state).toBe("open");
    expect(result.current.shouldRender).toBe(true);

    rerender({ isOpen: false });

    expect(result.current.state).toBe("closed");
    // shouldRender stays true until animationend fires
    expect(result.current.shouldRender).toBe(true);
  });

  it("shouldRender becomes false after animationend when closing", () => {
    const { result, rerender } = renderHook(
      ({ isOpen }) => useAnimatedPresence(isOpen),
      { initialProps: { isOpen: true } },
    );

    // Attach a mock DOM element to the ref
    const mockEl = document.createElement("div");
    Object.defineProperty(result.current.ref, "current", {
      value: mockEl,
      writable: true,
    });

    // Close
    rerender({ isOpen: false });

    expect(result.current.state).toBe("closed");
    expect(result.current.shouldRender).toBe(true);

    // Fire animationend on the element
    act(() => {
      mockEl.dispatchEvent(new Event("animationend"));
    });

    expect(result.current.shouldRender).toBe(false);
  });

  it("ref is defined", () => {
    const { result } = renderHook(() => useAnimatedPresence(true));

    expect(result.current.ref).toBeDefined();
    expect(result.current.ref.current).toBeNull();
  });
});
