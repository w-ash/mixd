import { ArrowUp, Heart, Music } from "lucide-react";
import { useCallback, useEffect, useRef } from "react";
import { Link, useSearchParams } from "react-router";
import { useGetConnectorsApiV1ConnectorsGet } from "@/api/generated/connectors/connectors";
import { useListTracksApiV1TracksGet } from "@/api/generated/tracks/tracks";
import { PageHeader } from "@/components/layout/PageHeader";
import { ConnectorIcon } from "@/components/shared/ConnectorIcon";
import { EmptyState } from "@/components/shared/EmptyState";
import { QueryErrorState } from "@/components/shared/QueryErrorState";
import { TablePagination } from "@/components/shared/TablePagination";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
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
import { formatArtists, formatDuration } from "@/lib/format";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 50;
const STAGGER_CAP = 15;

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
        {isActive && (
          <ArrowUp
            className={cn(
              "size-3 transition-transform duration-150",
              currentDir === "desc" && "rotate-180",
            )}
            aria-hidden="true"
          />
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

  // Keyset pagination: cache cursors from API responses for sequential nav.
  // Map: page number → cursor for the *next* page after that page.
  const cursorMapRef = useRef<Map<number, string>>(new Map());

  // Use cursor if available from the previous page (sequential next-page)
  const cursorForPage = cursorMapRef.current.get(pageParam - 1);

  const { data, isLoading, isError, error, isPlaceholderData } =
    useListTracksApiV1TracksGet(
      {
        q: querySearch,
        liked: likedFilter,
        connector: connectorParam ?? undefined,
        sort: sortParam,
        limit: PAGE_SIZE,
        offset: queryOffset,
        ...(cursorForPage ? { cursor: cursorForPage } : {}),
      },
      { query: { staleTime: 30_000, placeholderData: (prev) => prev } },
    );

  const response = data?.status === 200 ? data.data : undefined;
  const tracks = response?.data ?? [];
  const total = response?.total ?? 0;

  // Cache the next_cursor from the latest response
  const nextCursor = response?.next_cursor;
  useEffect(() => {
    if (nextCursor) {
      cursorMapRef.current.set(pageParam, nextCursor);
    }
  }, [nextCursor, pageParam]);

  const { page, totalPages, setPage } = usePagination(total);

  // Connectors list for filter dropdown
  const { data: connectorsData } = useGetConnectorsApiV1ConnectorsGet();
  const connectors = connectorsData?.status === 200 ? connectorsData.data : [];

  /** Update a URL search param, resetting page and cursor cache to 1 */
  const setFilter = useCallback(
    (key: string, value: string | null) => {
      cursorMapRef.current.clear();
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
    cursorMapRef.current.clear();
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
      <title>Library — Mixd</title>
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

        <Select
          value={likedParam ?? "all"}
          onValueChange={(value) =>
            setFilter("liked", value === "all" ? null : value)
          }
        >
          <SelectTrigger aria-label="Filter by liked status">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All tracks</SelectItem>
            <SelectItem value="true">Liked</SelectItem>
            <SelectItem value="false">Not liked</SelectItem>
          </SelectContent>
        </Select>

        <Select
          value={connectorParam ?? "all"}
          onValueChange={(value) =>
            setFilter("connector", value === "all" ? null : value)
          }
        >
          <SelectTrigger aria-label="Filter by connector">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All connectors</SelectItem>
            {connectors.map((c) => (
              <SelectItem key={c.name} value={c.name}>
                {c.name.charAt(0).toUpperCase() + c.name.slice(1)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {/* Loading */}
      {isLoading && <TrackTableSkeleton />}

      {/* Error */}
      {isError && (
        <QueryErrorState error={error} heading="Failed to load tracks" />
      )}

      {/* Empty state */}
      {!isLoading && !isError && tracks.length === 0 && (
        <EmptyState
          icon={<Music className="size-10" />}
          heading={hasFilters ? "No matching tracks" : "No tracks yet"}
          description={
            hasFilters
              ? "Try adjusting your search or filters."
              : "Import your music from Spotify or Last.fm to see your library here."
          }
          action={
            !hasFilters ? (
              <Button size="sm" asChild>
                <Link to="/settings/sync">Import Music</Link>
              </Button>
            ) : undefined
          }
        />
      )}

      {/* Track table */}
      {!isLoading && !isError && tracks.length > 0 && (
        <div
          className={cn(
            "transition-all duration-200",
            isPlaceholderData && "opacity-70 blur-[0.5px]",
          )}
        >
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-8">
                  <span className="sr-only">Liked</span>
                </TableHead>
                <SortableHead
                  field="title"
                  currentField={sortField}
                  currentDir={sortDir}
                  onSort={handleSort}
                >
                  Title
                </SortableHead>
                <SortableHead
                  field="artist"
                  currentField={sortField}
                  currentDir={sortDir}
                  onSort={handleSort}
                >
                  Artist
                </SortableHead>
                <TableHead className="w-48">Album</TableHead>
                <SortableHead
                  field="duration"
                  currentField={sortField}
                  currentDir={sortDir}
                  onSort={handleSort}
                  className="w-20 text-right"
                >
                  Duration
                </SortableHead>
                <TableHead className="w-24 text-center">Sources</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {tracks.map((track, index) => (
                <TableRow
                  key={track.id}
                  className="group relative"
                  style={
                    index < STAGGER_CAP
                      ? {
                          animation: `fade-in-row 300ms ease-out ${index * 20}ms both`,
                        }
                      : undefined
                  }
                >
                  {/* Liked */}
                  <TableCell className="relative w-8 text-center">
                    {/* Gold hover accent bar */}
                    <span className="absolute left-0 top-1 bottom-1 w-0.5 rounded-full bg-primary opacity-0 group-hover:opacity-100 transition-opacity" />
                    {track.is_liked && (
                      <Heart
                        className="mx-auto size-3.5 text-status-liked -translate-y-px"
                        aria-label="Liked"
                      />
                    )}
                  </TableCell>
                  {/* Title */}
                  <TableCell>
                    <Link
                      to={`/library/${track.id}`}
                      className="font-medium text-text hover:text-primary transition-colors"
                    >
                      {track.title}
                    </Link>
                  </TableCell>
                  {/* Artist */}
                  <TableCell className="text-text-muted text-sm truncate max-w-48">
                    {formatArtists(track.artists)}
                  </TableCell>
                  {/* Album */}
                  <TableCell className="text-text-muted text-sm truncate max-w-48">
                    {track.album ?? "\u2014"}
                  </TableCell>
                  {/* Duration */}
                  <TableCell className="text-right tabular-nums text-text-muted text-sm">
                    {formatDuration(track.duration_ms)}
                  </TableCell>
                  {/* Sources */}
                  <TableCell className="w-24">
                    <span className="flex justify-center gap-1">
                      {track.connector_names.map((name) => (
                        <ConnectorIcon key={name} name={name} labelHidden />
                      ))}
                    </span>
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
