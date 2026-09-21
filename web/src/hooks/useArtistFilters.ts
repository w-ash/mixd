/**
 * URL-backed filter state for the Artists page.
 *
 * Same contract as {@link useLibraryFilters}: every filter is a search param,
 * so a filtered view is shareable, survives a reload, and back/forward step
 * through filter history. This hook is the one place that knows how each
 * Artists filter maps onto its param — parsing on the way in, serializing on
 * the way out, and projecting the set onto `GET /artists` query params.
 *
 * Writes go through `useFilterState`, which drops `?page=` and navigates with
 * `replace` so a filter tweak is not a history entry of its own.
 */

import { useCallback, useMemo } from "react";

// The generated model barrel does not yet re-export the artist schemas, so
// these come straight from their generated modules.
import { ArtistSortBy } from "#/api/generated/model/artistSortBy";
import type { ListArtistsApiV1ArtistsGetParams } from "#/api/generated/model/listArtistsApiV1ArtistsGetParams";
import type { SortState } from "#/hooks/sort-param";
import { parseSortParam, toSortParam } from "#/hooks/sort-param";
import { useFilterState } from "#/hooks/useFilterState";
import { MIN_SEARCH_LENGTH, useTrackSearch } from "#/hooks/useTrackSearch";

/**
 * Sortable dimensions. `favorited_at` is descending-only on the backend and
 * has no column header; it is reachable from the URL alone.
 */
export type ArtistSortField = "name" | "track_count" | "favorited_at";

export const ARTIST_SORT_LABELS: Record<ArtistSortField, string> = {
  name: "Name",
  track_count: "Tracks",
  favorited_at: "Recently Favorited",
};

export type ArtistSort = SortState<ArtistSortField>;

const DEFAULT_SORT: ArtistSort = { field: "name", dir: "asc" };

/** The complete Artists filter set, parsed and coerced from the URL. */
export interface ArtistFilters {
  /** Applied search text — null until the input reaches two characters. */
  search: string | null;
  /** True when the list is narrowed to the user's favorites. */
  favorites: boolean;
  sort: ArtistSort;
}

export type SetArtistFilter = <K extends keyof ArtistFilters>(
  key: K,
  value: ArtistFilters[K] | null,
) => void;

/** The filter-derived half of `GET /artists` — pagination is the caller's. */
export type ArtistQueryParams = Pick<
  ListArtistsApiV1ArtistsGetParams,
  "search" | "favorites_only" | "sort"
>;

/** Search param backing each filter. */
const PARAM = {
  search: "q",
  favorites: "favorites",
  sort: "sort",
} as const satisfies Record<keyof ArtistFilters, string>;

/** Params the Dashboard and the toggle both write, and the URL may carry. */
const TRUTHY = new Set(["1", "true"]);

/** Every sort the API accepts — `favorited_at` is descending-only. */
const SORT_VALUES: ReadonlySet<string> = new Set(Object.values(ArtistSortBy));

/** `favorited_at_asc` parses cleanly but is not a value the API accepts. */
function isSortable(sort: ArtistSort): boolean {
  return SORT_VALUES.has(toSortParam(sort));
}

function toApiSort(sort: ArtistSort): ArtistSortBy {
  return toSortParam(sort) as ArtistSortBy;
}

export interface UseArtistFiltersOptions {
  /** Runs before every write so the caller can drop page-scoped state. */
  onMutate?: () => void;
}

export interface UseArtistFiltersResult {
  filters: ArtistFilters;
  setFilter: SetArtistFilter;
  /** Live search input value — leads `filters.search` while typing. */
  searchInput: string;
  /** True while the applied search lags behind the input. */
  isSearching: boolean;
  toQueryParams: () => ArtistQueryParams;
  /** The URL as this hook projects it, for anything else reading the params. */
  searchParams: URLSearchParams;
}

export function useArtistFilters({
  onMutate,
}: UseArtistFiltersOptions = {}): UseArtistFiltersResult {
  const { searchParams, setFilter: setParam } = useFilterState({ onMutate });

  const {
    search: searchInput,
    setSearch,
    deferredSearch,
    isSearching,
  } = useTrackSearch(searchParams.get(PARAM.search) ?? "");

  const filters = useMemo<ArtistFilters>(
    () => ({
      search:
        deferredSearch.length >= MIN_SEARCH_LENGTH ? deferredSearch : null,
      favorites: TRUTHY.has(searchParams.get(PARAM.favorites) ?? ""),
      sort: parseSortParam(
        searchParams.get(PARAM.sort),
        ARTIST_SORT_LABELS,
        DEFAULT_SORT,
        isSortable,
      ),
    }),
    [searchParams, deferredSearch],
  );

  const setFilter = useCallback<SetArtistFilter>(
    (key, value) => {
      if (key === "search") {
        // The input is local state; the param is what the query reads.
        const next = (value as string | null) ?? "";
        setSearch(next);
        setParam(PARAM.search, next === "" ? null : next);
        return;
      }
      if (key === "favorites") {
        setParam(PARAM.favorites, value ? "1" : null);
        return;
      }
      setParam(PARAM.sort, value ? toApiSort(value as ArtistSort) : null);
    },
    [setParam, setSearch],
  );

  const toQueryParams = useCallback(
    (): ArtistQueryParams => ({
      search: filters.search ?? undefined,
      favorites_only: filters.favorites || undefined,
      sort: toApiSort(filters.sort),
    }),
    [filters],
  );

  return {
    filters,
    setFilter,
    searchInput,
    isSearching,
    toQueryParams,
    searchParams,
  };
}
