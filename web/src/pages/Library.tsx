import { useCallback } from "react";
import { Link, useSearchParams } from "react-router";

import { useGetConnectorsApiV1ConnectorsGet } from "@/api/generated/connectors/connectors";
import { useListTracksApiV1TracksGet } from "@/api/generated/tracks/tracks";
import { PageHeader } from "@/components/layout/PageHeader";
import { ConnectorIcon } from "@/components/shared/ConnectorIcon";
import { EmptyState } from "@/components/shared/EmptyState";
import { TablePagination } from "@/components/shared/TablePagination";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { usePagination } from "@/hooks/usePagination";
import { useTrackSearch } from "@/hooks/useTrackSearch";
import { formatDuration } from "@/lib/format";

const PAGE_SIZE = 50;

type SortField = "title" | "artist" | "duration" | "added";
type SortDir = "asc" | "desc";

const SORT_LABELS: Record<SortField, string> = {
  title: "Title",
  artist: "Artist",
  duration: "Duration",
  added: "Added",
};

/** Map column name to API sort param */
function toSortParam(field: SortField, dir: SortDir): string {
  return `${field}_${dir}`;
}

/** Parse API sort param back to field + direction */
function parseSortParam(param: string): { field: SortField; dir: SortDir } {
  const lastUnderscore = param.lastIndexOf("_");
  if (lastUnderscore === -1) return { field: "title", dir: "asc" };
  const field = param.slice(0, lastUnderscore) as SortField;
  const dir = param.slice(lastUnderscore + 1) as SortDir;
  if (!SORT_LABELS[field] || (dir !== "asc" && dir !== "desc")) {
    return { field: "title", dir: "asc" };
  }
  return { field, dir };
}

function TrackTableSkeleton() {
  return (
    <div className="space-y-3">
      {Array.from({ length: 8 }).map((_, i) => (
        // biome-ignore lint/suspicious/noArrayIndexKey: static skeleton placeholders
        <div key={i} className="flex items-center gap-4">
          <Skeleton className="h-5 w-56" />
          <Skeleton className="h-5 w-32" />
          <Skeleton className="h-5 w-16" />
          <Skeleton className="h-5 w-24" />
          <Skeleton className="h-5 w-8" />
        </div>
      ))}
    </div>
  );
}

/** Sortable column header — clicking toggles direction or sets new sort */
function SortableHead({
  field,
  currentField,
  currentDir,
  onSort,
  className,
  children,
}: {
  field: SortField;
  currentField: SortField;
  currentDir: SortDir;
  onSort: (field: SortField, dir: SortDir) => void;
  className?: string;
  children: React.ReactNode;
}) {
  const isActive = field === currentField;
  const nextDir = isActive && currentDir === "asc" ? "desc" : "asc";
  const arrow = isActive ? (currentDir === "asc" ? " \u2191" : " \u2193") : "";

  return (
    <TableHead
      className={className}
      aria-sort={
        isActive ? (currentDir === "asc" ? "ascending" : "descending") : "none"
      }
    >
      <button
        type="button"
        className="inline-flex items-center gap-1 hover:text-text transition-colors"
        onClick={() => onSort(field, nextDir)}
        aria-label={`Sort by ${SORT_LABELS[field]} ${nextDir === "asc" ? "ascending" : "descending"}`}
      >
        {children}
        {arrow && (
          <span className="text-text-muted" aria-hidden="true">
            {arrow}
          </span>
        )}
      </button>
    </TableHead>
  );
}

export function Library() {
  const [searchParams, setSearchParams] = useSearchParams();
  const { search, setSearch, deferredSearch, isSearching } = useTrackSearch(
    searchParams.get("q") ?? "",
  );

  // URL-driven filter state
  const likedParam = searchParams.get("liked");
  const connectorParam = searchParams.get("connector");
  const sortParam = searchParams.get("sort") ?? "title_asc";
  const { field: sortField, dir: sortDir } = parseSortParam(sortParam);

  // Build query params for the API
  const querySearch = deferredSearch.length >= 2 ? deferredSearch : undefined;
  const likedFilter =
    likedParam === "true" ? true : likedParam === "false" ? false : undefined;

  // Pagination — offset derived from URL ?page= before query fires;
  // usePagination called after query for totalPages/setPage (needs total).
  const pageParam = Number(searchParams.get("page") ?? "1");
  const queryOffset = (pageParam - 1) * PAGE_SIZE;

  const { data, isLoading, isError, error, isPlaceholderData } =
    useListTracksApiV1TracksGet(
      {
        q: querySearch,
        liked: likedFilter,
        connector: connectorParam ?? undefined,
        sort: sortParam,
        limit: PAGE_SIZE,
        offset: queryOffset,
      },
      { query: { staleTime: 30_000, placeholderData: (prev) => prev } },
    );

  const response = data?.status === 200 ? data.data : undefined;
  const tracks = response?.data ?? [];
  const total = response?.total ?? 0;

  const { page, totalPages, setPage } = usePagination(total);

  // Connectors list for filter dropdown
  const { data: connectorsData } = useGetConnectorsApiV1ConnectorsGet();
  const connectors = connectorsData?.status === 200 ? connectorsData.data : [];

  /** Update a URL search param, resetting page to 1 */
  const setFilter = useCallback(
    (key: string, value: string | null) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (value === null || value === "") {
            next.delete(key);
          } else {
            next.set(key, value);
          }
          next.delete("page"); // reset pagination on filter change
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  const handleSort = useCallback(
    (field: SortField, dir: SortDir) => {
      setFilter("sort", toSortParam(field, dir));
    },
    [setFilter],
  );

  const handleSearchChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const value = e.target.value;
    setSearch(value);
    // Sync to URL for deep-linking (debounced via deferred value for API)
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (value === "") {
          next.delete("q");
        } else {
          next.set("q", value);
        }
        next.delete("page");
        return next;
      },
      { replace: true },
    );
  };

  const hasFilters = querySearch || likedParam || connectorParam;

  return (
    <div>
      <PageHeader
        title="Library"
        description={
          total > 0
            ? `${total.toLocaleString()} track${total !== 1 ? "s" : ""} across all services.`
            : "Your complete track collection."
        }
      />

      {/* Search + Filters */}
      <div className="mb-6 flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-48">
          <Input
            type="search"
            placeholder="Search tracks, artists, albums..."
            value={search}
            onChange={handleSearchChange}
            aria-label="Search tracks"
            className={isSearching ? "opacity-70" : ""}
          />
          {isSearching && (
            <span
              className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-text-muted"
              aria-live="polite"
            >
              Searching...
            </span>
          )}
        </div>

        <select
          value={likedParam ?? ""}
          onChange={(e) => setFilter("liked", e.target.value || null)}
          className="h-9 rounded-md border border-input bg-transparent px-3 font-display text-sm leading-none [text-box:trim-both_cap_alphabetic] text-text"
          aria-label="Filter by liked status"
        >
          <option value="">All tracks</option>
          <option value="true">Liked</option>
          <option value="false">Not liked</option>
        </select>

        <select
          value={connectorParam ?? ""}
          onChange={(e) => setFilter("connector", e.target.value || null)}
          className="h-9 rounded-md border border-input bg-transparent px-3 font-display text-sm leading-none [text-box:trim-both_cap_alphabetic] text-text"
          aria-label="Filter by connector"
        >
          <option value="">All connectors</option>
          {connectors.map((c) => (
            <option key={c.name} value={c.name}>
              {c.name.charAt(0).toUpperCase() + c.name.slice(1)}
            </option>
          ))}
        </select>
      </div>

      {/* Loading */}
      {isLoading && <TrackTableSkeleton />}

      {/* Error */}
      {isError && (
        <EmptyState
          icon="!"
          heading="Failed to load tracks"
          description={
            error instanceof Error
              ? error.message
              : "An unexpected error occurred."
          }
        />
      )}

      {/* Empty state */}
      {!isLoading && !isError && tracks.length === 0 && (
        <EmptyState
          icon="♪"
          heading={hasFilters ? "No matching tracks" : "No tracks yet"}
          description={
            hasFilters
              ? "Try adjusting your search or filters."
              : "Import your music from Spotify or Last.fm to see your library here."
          }
        />
      )}

      {/* Track table */}
      {!isLoading && !isError && tracks.length > 0 && (
        <div
          className={`transition-opacity ${isPlaceholderData ? "opacity-70" : ""}`}
        >
          <Table>
            <TableHeader>
              <TableRow>
                <SortableHead
                  field="title"
                  currentField={sortField}
                  currentDir={sortDir}
                  onSort={handleSort}
                >
                  Title
                </SortableHead>
                <TableHead className="w-40">Album</TableHead>
                <SortableHead
                  field="duration"
                  currentField={sortField}
                  currentDir={sortDir}
                  onSort={handleSort}
                  className="w-24 text-right"
                >
                  Duration
                </SortableHead>
                <TableHead className="w-40">Connectors</TableHead>
                <TableHead className="w-16 text-center">Liked</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {tracks.map((track) => (
                <TableRow key={track.id} className="group">
                  <TableCell>
                    <Link
                      to={`/library/${track.id}`}
                      className="font-medium text-text hover:text-primary transition-colors"
                    >
                      {track.title}
                    </Link>
                    <p className="mt-0.5 text-xs text-text-muted line-clamp-1">
                      {track.artists.map((a) => a.name).join(", ")}
                    </p>
                  </TableCell>
                  <TableCell className="text-text-muted text-sm truncate max-w-40">
                    {track.album ?? "\u2014"}
                  </TableCell>
                  <TableCell className="text-right tabular-nums text-text-muted text-sm">
                    {formatDuration(track.duration_ms)}
                  </TableCell>
                  <TableCell>
                    <div className="flex gap-2">
                      {track.connector_names.map((name) => (
                        <ConnectorIcon
                          key={name}
                          name={name}
                          className="text-xs"
                        />
                      ))}
                    </div>
                  </TableCell>
                  <TableCell className="text-center">
                    {track.is_liked && (
                      <span
                        className="text-status-liked"
                        role="img"
                        title="Liked"
                        aria-label="Liked"
                      >
                        &#9829;
                      </span>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>

          <TablePagination
            page={page}
            totalPages={totalPages}
            total={total}
            limit={PAGE_SIZE}
            onPageChange={setPage}
          />
        </div>
      )}
    </div>
  );
}
