/**
 * URL-backed filter state for the Library page.
 *
 * Every Library filter is a search param, so the URL is the single source of
 * truth: a filtered view is shareable, survives a reload, and back/forward
 * step through filter history. This hook is the one place that knows how each
 * filter maps onto its param — parsing on the way in, serializing on the way
 * out, and projecting the set onto `GET /tracks` query params.
 *
 * Consumers read one typed {@link LibraryFilters} object and write through
 * `setFilter` / `setFilters`, so the filter panel and the active-filter chips
 * take three filter props between them instead of nine values plus nine
 * change handlers. Adding a filter is one entry in `PARAM`, one field on
 * `LibraryFilters`, one line in the parser and one in `toQueryParams`.
 *
 * Writes go through `useFilterState`, which drops `?page=` and navigates with
 * `replace` so a filter tweak is not a history entry of its own.
 */

import { useCallback, useMemo } from "react";

import type {
  ListTracksApiV1TracksGetParams,
  TrackSortBy,
} from "#/api/generated/model";
import type { PreferenceState } from "#/components/shared/PreferenceToggle";
import { useFilterState } from "#/hooks/useFilterState";
import { useTrackSearch } from "#/hooks/useTrackSearch";
import type { TagMatchMode } from "#/lib/filters-to-workflow";
import { parsePreferenceParam } from "#/lib/filters-to-workflow";
import { countPlayFilters } from "#/lib/play-filters";

/** Shortest input that reaches the API — below this the search is not applied. */
const MIN_SEARCH_LENGTH = 2;

// No artist sort until artists are first-class (v0.12.1) — `artists_text` is a
// joined display string, so ordering by it sorts by "Bowie, Eno", not by artist.
export type SortField =
  | "title"
  | "duration"
  | "added"
  | "plays"
  | "last_played";
export type SortDir = "asc" | "desc";

export const SORT_LABELS: Record<SortField, string> = {
  title: "Title",
  duration: "Duration",
  added: "Added",
  plays: "Plays",
  last_played: "Last Played",
};

export interface LibrarySort {
  field: SortField;
  dir: SortDir;
}

const DEFAULT_SORT: LibrarySort = { field: "last_played", dir: "desc" };

/** The complete Library filter set, parsed and coerced from the URL. */
export interface LibraryFilters {
  /** Applied search text — null until the input reaches two characters. */
  search: string | null;
  preference: PreferenceState | null;
  liked: "true" | "false" | null;
  connector: string | null;
  tags: string[];
  tagMode: TagMatchMode;
  minPlays: number | null;
  neverPlayed: boolean;
  playedWithin: number | null;
  notPlayedWithin: number | null;
  sort: LibrarySort;
}

/** Filters written one at a time, or together via {@link setFilters}. */
type ScalarFilters = Omit<LibraryFilters, "tags">;

/** A partial write. `null` and `undefined` both clear the filter. */
export type LibraryFilterPatch = {
  [K in keyof ScalarFilters]?: ScalarFilters[K] | null;
};

export type SetLibraryFilter = <K extends keyof LibraryFilters>(
  key: K,
  value: LibraryFilters[K] | null,
) => void;

export type SetLibraryFilters = (patch: LibraryFilterPatch) => void;

/** The filter-derived half of `GET /tracks` — pagination is the caller's. */
export type LibraryQueryParams = Pick<
  ListTracksApiV1TracksGetParams,
  | "q"
  | "liked"
  | "connector"
  | "preference"
  | "tag"
  | "tag_mode"
  | "min_plays"
  | "played_within"
  | "not_played_within"
  | "never_played"
  | "sort"
>;

/** Search param backing each filter. */
const PARAM = {
  search: "q",
  preference: "preference",
  liked: "liked",
  connector: "connector",
  tags: "tag",
  tagMode: "tag_mode",
  minPlays: "min_plays",
  neverPlayed: "never_played",
  playedWithin: "played_within",
  notPlayedWithin: "not_played_within",
  sort: "sort",
} as const satisfies Record<keyof LibraryFilters, string>;

/** Numeric param, or null when absent or not a finite number. */
function toNumber(raw: string | null): number | null {
  if (raw === null || raw === "") return null;
  const n = Number(raw);
  return Number.isFinite(n) ? n : null;
}

function toSortParam({ field, dir }: LibrarySort): TrackSortBy {
  return `${field}_${dir}`;
}

/** Parse `?sort=`, falling back to the default for anything unrecognized. */
function parseSort(raw: string | null): LibrarySort {
  if (raw === null) return DEFAULT_SORT;
  const split = raw.lastIndexOf("_");
  if (split === -1) return DEFAULT_SORT;
  const field = raw.slice(0, split) as SortField;
  const dir = raw.slice(split + 1) as SortDir;
  if (!SORT_LABELS[field] || (dir !== "asc" && dir !== "desc")) {
    return DEFAULT_SORT;
  }
  return { field, dir };
}

type ScalarValue = ScalarFilters[keyof ScalarFilters];

/** Filter value to param value. `null` deletes the param. */
function serialize(
  key: keyof ScalarFilters,
  value: ScalarValue | null | undefined,
): string | null {
  if (value === null || value === undefined) return null;
  switch (key) {
    case "sort":
      return toSortParam(value as LibrarySort);
    // "and" is the default match mode, so it carries no param.
    case "tagMode":
      return value === "or" ? "or" : null;
    case "neverPlayed":
      return value ? "true" : null;
    default:
      return String(value);
  }
}

/** `countPlayFilters` speaks raw params; the play filters are parsed numbers. */
function asParam(value: number | null): string | null {
  return value === null ? null : String(value);
}

/**
 * How many filter groups the workflow serializer can express. Save as
 * Workflow keys on this subset, so it is counted separately from the play
 * filters that have no workflow node yet. Tags count once however many are
 * applied — the number means "how many filter groups", not "how many values".
 */
function countMappableFilters(filters: LibraryFilters): number {
  return (
    (filters.preference ? 1 : 0) +
    (filters.liked ? 1 : 0) +
    (filters.connector ? 1 : 0) +
    (filters.tags.length > 0 ? 1 : 0)
  );
}

export interface UseLibraryFiltersOptions {
  /** Runs before every write so the caller can drop page-scoped state. */
  onMutate?: () => void;
}

export interface UseLibraryFiltersResult {
  filters: LibraryFilters;
  setFilter: SetLibraryFilter;
  /** Write several filters in ONE navigation. Tags go through `setFilter`. */
  setFilters: SetLibraryFilters;
  /** Drop every filter, including the search input. */
  clear: () => void;
  /** Active filter groups, including the play filters. */
  activeCount: number;
  /** Active filter groups the workflow serializer can express. */
  mappableCount: number;
  /** Live search input value — leads `filters.search` while typing. */
  searchInput: string;
  /** True while the applied search lags behind the input. */
  isSearching: boolean;
  toQueryParams: () => LibraryQueryParams;
  /** Raw params, for page and cursor bookkeeping the caller owns. */
  searchParams: URLSearchParams;
}

export function useLibraryFilters({
  onMutate,
}: UseLibraryFiltersOptions = {}): UseLibraryFiltersResult {
  const {
    searchParams,
    setFilter: setParam,
    setFilters: setParams,
    setMultiFilter,
    clearAll,
  } = useFilterState({ onMutate });

  const {
    search: searchInput,
    setSearch,
    deferredSearch,
    isSearching,
  } = useTrackSearch(searchParams.get(PARAM.search) ?? "");

  // `getAll` builds a fresh array each call — memoize it so `filters` and every
  // consumer downstream keep a stable identity between navigations.
  const tags = useMemo(() => searchParams.getAll(PARAM.tags), [searchParams]);

  const filters = useMemo<LibraryFilters>(() => {
    const liked = searchParams.get(PARAM.liked);
    return {
      search:
        deferredSearch.length >= MIN_SEARCH_LENGTH ? deferredSearch : null,
      preference: parsePreferenceParam(searchParams.get(PARAM.preference)),
      liked: liked === "true" || liked === "false" ? liked : null,
      connector: searchParams.get(PARAM.connector),
      tags,
      tagMode: searchParams.get(PARAM.tagMode) === "or" ? "or" : "and",
      minPlays: toNumber(searchParams.get(PARAM.minPlays)),
      neverPlayed: searchParams.get(PARAM.neverPlayed) === "true",
      playedWithin: toNumber(searchParams.get(PARAM.playedWithin)),
      notPlayedWithin: toNumber(searchParams.get(PARAM.notPlayedWithin)),
      sort: parseSort(searchParams.get(PARAM.sort)),
    };
  }, [searchParams, tags, deferredSearch]);

  const setFilter = useCallback<SetLibraryFilter>(
    (key, value) => {
      if (key === "tags") {
        setMultiFilter(PARAM.tags, (value as string[] | null) ?? []);
        return;
      }
      // The input is local state; the param is what the query reads.
      if (key === "search") setSearch((value as string | null) ?? "");
      const field = key as keyof ScalarFilters;
      setParam(PARAM[field], serialize(field, value as ScalarValue));
    },
    [setMultiFilter, setParam, setSearch],
  );

  const setFilters = useCallback<SetLibraryFilters>(
    (patch) => {
      const updates: Record<string, string | null> = {};
      for (const [key, value] of Object.entries(patch)) {
        const field = key as keyof ScalarFilters;
        if (field === "search") setSearch((value as string | null) ?? "");
        updates[PARAM[field]] = serialize(field, value);
      }
      setParams(updates);
    },
    [setParams, setSearch],
  );

  const clear = useCallback(() => {
    setSearch("");
    clearAll();
  }, [clearAll, setSearch]);

  const mappableCount = countMappableFilters(filters);
  const activeCount =
    mappableCount +
    countPlayFilters({
      minPlays: asParam(filters.minPlays),
      neverPlayed: filters.neverPlayed,
      playedWithin: asParam(filters.playedWithin),
      notPlayedWithin: asParam(filters.notPlayedWithin),
    });

  const toQueryParams = useCallback(
    (): LibraryQueryParams => ({
      q: filters.search ?? undefined,
      liked: filters.liked === null ? undefined : filters.liked === "true",
      connector: filters.connector ?? undefined,
      preference: filters.preference ?? undefined,
      tag: filters.tags.length > 0 ? filters.tags : undefined,
      tag_mode: filters.tagMode,
      min_plays: filters.minPlays ?? undefined,
      played_within: filters.playedWithin ?? undefined,
      not_played_within: filters.notPlayedWithin ?? undefined,
      never_played: filters.neverPlayed || undefined,
      sort: toSortParam(filters.sort),
    }),
    [filters],
  );

  return {
    filters,
    setFilter,
    setFilters,
    clear,
    activeCount,
    mappableCount,
    searchInput,
    isSearching,
    toQueryParams,
    searchParams,
  };
}
