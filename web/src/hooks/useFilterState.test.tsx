import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";

import { useFilterState } from "./useFilterState";

function wrapper(initialUrl: string) {
  return ({ children }: { children: ReactNode }) => (
    <MemoryRouter initialEntries={[initialUrl]}>{children}</MemoryRouter>
  );
}

describe("useFilterState", () => {
  it("setFilter writes ?key=value and clears the page param", () => {
    const { result } = renderHook(() => useFilterState(), {
      wrapper: wrapper("/library?page=3"),
    });

    act(() => result.current.setFilter("preference", "star"));

    expect(result.current.searchParams.get("preference")).toBe("star");
    expect(result.current.searchParams.get("page")).toBeNull();
  });

  it("setFilter with null removes the key", () => {
    const { result } = renderHook(() => useFilterState(), {
      wrapper: wrapper("/library?preference=star"),
    });

    act(() => result.current.setFilter("preference", null));

    expect(result.current.searchParams.get("preference")).toBeNull();
  });

  it("setFilter with empty string removes the key", () => {
    const { result } = renderHook(() => useFilterState(), {
      wrapper: wrapper("/library?q=abc"),
    });

    act(() => result.current.setFilter("q", ""));

    expect(result.current.searchParams.get("q")).toBeNull();
  });

  it("setMultiFilter replaces the full set of values for a repeating param", () => {
    const { result } = renderHook(() => useFilterState(), {
      wrapper: wrapper("/library?tag=mood:chill&tag=energy:low"),
    });

    act(() => result.current.setMultiFilter("tag", ["mood:upbeat"]));

    expect(result.current.searchParams.getAll("tag")).toEqual(["mood:upbeat"]);
  });

  it("clearAll empties the search params", () => {
    const { result } = renderHook(() => useFilterState(), {
      wrapper: wrapper("/library?preference=star&tag=mood:chill&page=2"),
    });

    act(() => result.current.clearAll());

    expect(result.current.searchParams.toString()).toBe("");
  });

  it("fires onMutate before each write", () => {
    const onMutate = vi.fn();
    const { result } = renderHook(() => useFilterState({ onMutate }), {
      wrapper: wrapper("/library?page=3"),
    });

    act(() => result.current.setFilter("liked", "true"));
    act(() => result.current.setMultiFilter("tag", ["a"]));
    act(() => result.current.clearAll());

    expect(onMutate).toHaveBeenCalledTimes(3);
  });
});
