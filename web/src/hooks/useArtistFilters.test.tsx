import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";

import { useArtistFilters } from "./useArtistFilters";

function wrapper(initialUrl: string) {
  return ({ children }: { children: ReactNode }) => (
    <MemoryRouter initialEntries={[initialUrl]}>{children}</MemoryRouter>
  );
}

function render(url = "/artists", onMutate?: () => void) {
  return renderHook(() => useArtistFilters({ onMutate }), {
    wrapper: wrapper(url),
  });
}

describe("useArtistFilters — parsing", () => {
  it("coerces every param into the typed filter set", () => {
    const { result } = render(
      "/artists?q=bowie&favorites=1&sort=track_count_desc",
    );

    expect(result.current.filters).toEqual({
      search: "bowie",
      favorites: true,
      sort: { field: "track_count", dir: "desc" },
    });
  });

  it("accepts either truthy spelling of the favorites param", () => {
    // The Dashboard links with `?favorites=1`; a hand-edited URL may say `true`.
    expect(
      render("/artists?favorites=true").result.current.filters.favorites,
    ).toBe(true);
    expect(
      render("/artists?favorites=0").result.current.filters.favorites,
    ).toBe(false);
  });

  it("normalizes garbage sorts instead of passing them downstream", () => {
    for (const raw of ["nonsense", "name_sideways", "favorited_at_asc"]) {
      expect(
        render(`/artists?sort=${raw}`).result.current.filters.sort,
      ).toEqual({
        field: "name",
        dir: "asc",
      });
    }
  });

  it("applies the search only once the input reaches two characters", () => {
    const { result } = render("/artists?q=b");
    expect(result.current.searchInput).toBe("b");
    expect(result.current.filters.search).toBeNull();

    act(() => result.current.setFilter("search", "bo"));
    expect(result.current.filters.search).toBe("bo");
  });
});

describe("useArtistFilters — writes", () => {
  it("projects the filter set onto GET /artists params", () => {
    const { result } = render("/artists?q=eno&favorites=1&sort=name_desc");

    expect(result.current.toQueryParams()).toEqual({
      search: "eno",
      favorites_only: true,
      sort: "name_desc",
    });
  });

  it("omits favorites_only when the filter is off", () => {
    const { result } = render("/artists");

    expect(result.current.toQueryParams()).toEqual({
      search: undefined,
      favorites_only: undefined,
      sort: "name_asc",
    });
  });

  it("drops the param rather than writing a falsy value", () => {
    const { result } = render("/artists?favorites=1&page=3");

    act(() => result.current.setFilter("favorites", false));

    expect(result.current.searchParams.get("favorites")).toBeNull();
    // Every filter write resets paging — page 3 of the old result set is gone.
    expect(result.current.searchParams.get("page")).toBeNull();
  });

  it("runs onMutate before every write so the caller can drop page state", () => {
    const onMutate = vi.fn();
    const { result } = render("/artists", onMutate);

    act(() => result.current.setFilter("sort", { field: "name", dir: "desc" }));
    act(() => result.current.setFilter("favorites", true));
    act(() => result.current.clear());

    expect(onMutate).toHaveBeenCalledTimes(3);
  });

  it("clears the search input alongside the params", () => {
    const { result } = render("/artists?q=bowie&favorites=1");

    act(() => result.current.clear());

    expect(result.current.searchInput).toBe("");
    expect(result.current.filters).toEqual({
      search: null,
      favorites: false,
      sort: { field: "name", dir: "asc" },
    });
  });
});
