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
import { useFilterState } from "#/hooks/useFilterState";
import { useTrackSearch } from "#/hooks/useTrackSearch";

/** Shortest input that reaches the API — below this the search is not applied. */
const MIN_SEARCH_LENGTH = 2;

/**
 * Sortable dimensions. `favorited_at` is descending-only on the backend and
 * has no column header; it is reachable from the URL alone.
 */
export type ArtistSortField = "name" | "track_count" | "favorited_at";
export type ArtistSortDir = "asc" | "desc";

export const ARTIST_SORT_LABELS: Record<ArtistSortField, string> = {
  name: "Name",
  track_count: "Tracks",
  favorited_at: "Recently Favorited",
};

export interface ArtistSort {
  field: ArtistSortField;
  dir: ArtistSortDir;
}

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

function isSortable(sort: ArtistSort): boolean {
  return SORT_VALUES.has(`${sort.field}_${sort.dir}`);
}

function toSortParam({ field, dir }: ArtistSort): ArtistSortBy {
  return `${field}_${dir}` as ArtistSortBy;
}

/** Parse `?sort=`, falling back to the default for anything the API rejects. */
function parseSort(raw: string | null): ArtistSort {
  if (raw === null) return DEFAULT_SORT;
  const split = raw.lastIndexOf("_");
  if (split === -1) return DEFAULT_SORT;
  const field = raw.slice(0, split) as ArtistSortField;
  const dir = raw.slice(split + 1) as ArtistSortDir;
  if (!ARTIST_SORT_LABELS[field] || (dir !== "asc" && dir !== "desc")) {
    return DEFAULT_SORT;
  }
  // `favorited_at_asc` parses cleanly but is not a value the API accepts.
  return isSortable({ field, dir }) ? { field, dir } : DEFAULT_SORT;
}

export interface UseArtistFiltersOptions {
  /** Runs before every write so the caller can drop page-scoped state. */
  onMutate?: () => void;
}

export interface UseArtistFiltersResult {
  filters: ArtistFilters;
  setFilter: SetArtistFilter;
  /** Drop every filter, including the search input. */
  clear: () => void;
  /** Live search input value — leads `filters.search` while typing. */
  searchInput: string;
  /** True while the applied search lags behind the input. */
  isSearching: boolean;
  toQueryParams: () => ArtistQueryParams;
  /** Raw params, for page and cursor bookkeeping the caller owns. */
  searchParams: URLSearchParams;
}

export function useArtistFilters({
  onMutate,
}: UseArtistFiltersOptions = {}): UseArtistFiltersResult {
  const {
    searchParams,
    setFilter: setParam,
    clearAll,
  } = useFilterState({ onMutate });

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
      sort: parseSort(searchParams.get(PARAM.sort)),
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
      setParam(PARAM.sort, value ? toSortParam(value as ArtistSort) : null);
    },
    [setParam, setSearch],
  );

  const clear = useCallback(() => {
    setSearch("");
    clearAll();
  }, [clearAll, setSearch]);

  const toQueryParams = useCallback(
    (): ArtistQueryParams => ({
      search: filters.search ?? undefined,
      favorites_only: filters.favorites || undefined,
      sort: toSortParam(filters.sort),
    }),
    [filters],
  );

  return {
    filters,
    setFilter,
    clear,
    searchInput,
    isSearching,
    toQueryParams,
    searchParams,
  };
}
