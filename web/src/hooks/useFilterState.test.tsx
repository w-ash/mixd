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

  // Pins the constraint that makes `setFilters` necessary. `setSearchParams`
  // does not queue like `setState`: react-router navigates immediately against
  // the params captured at the current render, so two writes sharing a render
  // both branch off the same base and the last one wins. Two writes in
  // SEPARATE act() blocks do compose — a re-render lands between them — which
  // is why a per-call test suite can pass while real handlers silently drop
  // writes. Anything touching two keys must use `setFilters`.
  it("drops the earlier write when two setFilter calls share a render", () => {
    const { result } = renderHook(() => useFilterState(), {
      wrapper: wrapper("/plays"),
    });

    act(() => {
      result.current.setFilter("from", "2026-01-01");
      result.current.setFilter("to", "2026-02-01");
    });

    expect(result.current.searchParams.get("from")).toBeNull();
    expect(result.current.searchParams.get("to")).toBe("2026-02-01");
  });

  it("setFilters keeps both keys where two setFilter calls would not", () => {
    const { result } = renderHook(() => useFilterState(), {
      wrapper: wrapper("/plays"),
    });

    act(() =>
      result.current.setFilters({ from: "2026-01-01", to: "2026-02-01" }),
    );

    expect(result.current.searchParams.get("from")).toBe("2026-01-01");
    expect(result.current.searchParams.get("to")).toBe("2026-02-01");
  });

  it("setFilters writes every key in one navigation", () => {
    const { result } = renderHook(() => useFilterState(), {
      wrapper: wrapper("/library?page=4"),
    });

    act(() =>
      result.current.setFilters({ min_plays: "10", never_played: "true" }),
    );

    expect(result.current.searchParams.get("min_plays")).toBe("10");
    expect(result.current.searchParams.get("never_played")).toBe("true");
    expect(result.current.searchParams.get("page")).toBeNull();
  });

  it("setFilters sets and deletes in the same call", () => {
    const { result } = renderHook(() => useFilterState(), {
      wrapper: wrapper("/library?min_plays=10&liked=true"),
    });

    act(() =>
      result.current.setFilters({ min_plays: null, never_played: "true" }),
    );

    expect(result.current.searchParams.get("min_plays")).toBeNull();
    expect(result.current.searchParams.get("never_played")).toBe("true");
    expect(result.current.searchParams.get("liked")).toBe("true");
  });

  it("setFilters treats an empty string as a delete", () => {
    const { result } = renderHook(() => useFilterState(), {
      wrapper: wrapper("/library?q=abc&liked=true"),
    });

    act(() => result.current.setFilters({ q: "", liked: null }));

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
