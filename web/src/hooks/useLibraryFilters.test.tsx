import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";

import { useLibraryFilters } from "./useLibraryFilters";

function wrapper(initialUrl: string) {
  return ({ children }: { children: ReactNode }) => (
    <MemoryRouter initialEntries={[initialUrl]}>{children}</MemoryRouter>
  );
}

function render(url = "/library", onMutate?: () => void) {
  return renderHook(() => useLibraryFilters({ onMutate }), {
    wrapper: wrapper(url),
  });
}

describe("useLibraryFilters — parsing", () => {
  it("coerces every param into the typed filter set", () => {
    const { result } = render(
      "/library?q=bowie&preference=star&liked=true&connector=spotify" +
        "&tag=mood:chill&tag=energy:low&tag_mode=or&min_plays=10" +
        "&played_within=7&sort=title_asc",
    );

    expect(result.current.filters).toMatchObject({
      preference: "star",
      liked: "true",
      connector: "spotify",
      tags: ["mood:chill", "energy:low"],
      tagMode: "or",
      minPlays: 10,
      neverPlayed: false,
      playedWithin: 7,
      notPlayedWithin: null,
      sort: { field: "title", dir: "asc" },
    });
  });

  it("normalizes garbage values instead of passing them downstream", () => {
    const { result } = render(
      "/library?preference=garbage&liked=maybe&min_plays=abc&sort=nonsense",
    );

    expect(result.current.filters.preference).toBeNull();
    expect(result.current.filters.liked).toBeNull();
    expect(result.current.filters.minPlays).toBeNull();
    expect(result.current.filters.sort).toEqual({
      field: "last_played",
      dir: "desc",
    });
  });

  it("applies the search only once the input reaches two characters", () => {
    const { result } = render("/library?q=b");
    expect(result.current.searchInput).toBe("b");
    expect(result.current.filters.search).toBeNull();

    act(() => result.current.setFilter("search", "bo"));
    expect(result.current.filters.search).toBe("bo");
  });
});

describe("useLibraryFilters — counts", () => {
  it("counts each filter group once and tags as one group", () => {
    const { result } = render(
      "/library?preference=star&liked=false&connector=spotify" +
        "&tag=mood:chill&tag=energy:low",
    );
    expect(result.current.mappableCount).toBe(4);
    expect(result.current.activeCount).toBe(4);
  });

  it("adds the play filters on top of the workflow-mappable ones", () => {
    // Play count and recency are one group each however they are expressed.
    const { result } = render(
      "/library?preference=star&min_plays=10&never_played=true&played_within=7",
    );
    expect(result.current.mappableCount).toBe(1);
    expect(result.current.activeCount).toBe(3);
  });

  it("is 0 for an unfiltered library", () => {
    const { result } = render("/library?page=2&sort=title_asc");
    expect(result.current.activeCount).toBe(0);
  });
});

describe("useLibraryFilters — writes", () => {
  it("setFilter serializes the value and drops the page param", () => {
    const { result } = render("/library?page=4");

    act(() => result.current.setFilter("minPlays", 25));

    expect(result.current.searchParams.get("min_plays")).toBe("25");
    expect(result.current.searchParams.get("page")).toBeNull();
  });

  it("writes tags as repeated params and clears them with an empty list", () => {
    const { result } = render("/library?tag=mood:chill");

    act(() => result.current.setFilter("tags", ["a", "b"]));
    expect(result.current.searchParams.getAll("tag")).toEqual(["a", "b"]);

    act(() => result.current.setFilter("tags", []));
    expect(result.current.searchParams.getAll("tag")).toEqual([]);
  });

  it("omits the default 'and' tag mode from the URL", () => {
    const { result } = render("/library?tag_mode=or");

    act(() => result.current.setFilter("tagMode", "and"));
    expect(result.current.searchParams.get("tag_mode")).toBeNull();
    expect(result.current.filters.tagMode).toBe("and");
  });

  it("setFilters writes several params in one navigation", () => {
    // Two back-to-back single writes would branch off the same base and the
    // second would silently discard the first.
    const { result } = render("/library?min_plays=10&never_played=true");

    act(() =>
      result.current.setFilters({ minPlays: null, neverPlayed: false }),
    );

    expect(result.current.searchParams.get("min_plays")).toBeNull();
    expect(result.current.searchParams.get("never_played")).toBeNull();
  });

  it("clear drops every param and empties the search input", () => {
    const { result } = render("/library?q=bowie&preference=star&page=2");

    act(() => result.current.clear());

    expect(result.current.searchParams.toString()).toBe("");
    expect(result.current.searchInput).toBe("");
    expect(result.current.filters.search).toBeNull();
  });

  it("runs onMutate before every write", () => {
    const onMutate = vi.fn();
    const { result } = render("/library", onMutate);

    act(() => result.current.setFilter("connector", "spotify"));
    act(() => result.current.setFilter("tags", ["x"]));
    act(() => result.current.clear());

    expect(onMutate).toHaveBeenCalledTimes(3);
  });
});

describe("useLibraryFilters — toQueryParams", () => {
  it("projects the filter set onto the tracks endpoint's params", () => {
    const { result } = render(
      "/library?liked=false&connector=spotify&preference=yah" +
        "&tag=mood:chill&tag_mode=or&min_plays=10&not_played_within=730" +
        "&sort=plays_desc",
    );

    expect(result.current.toQueryParams()).toEqual({
      q: undefined,
      liked: false,
      connector: "spotify",
      preference: "yah",
      tag: ["mood:chill"],
      tag_mode: "or",
      min_plays: 10,
      played_within: undefined,
      not_played_within: 730,
      never_played: undefined,
      sort: "plays_desc",
    });
  });

  it("omits absent filters rather than sending nulls", () => {
    const { result } = render("/library");
    const params = result.current.toQueryParams();

    expect(params.liked).toBeUndefined();
    expect(params.tag).toBeUndefined();
    expect(params.never_played).toBeUndefined();
    // Mode and sort always ride along so the API never guesses.
    expect(params.tag_mode).toBe("and");
    expect(params.sort).toBe("last_played_desc");
  });

  it("sends a zero minimum play count instead of dropping it", () => {
    const { result } = render("/library?min_plays=0");
    expect(result.current.toQueryParams().min_plays).toBe(0);
  });
});
