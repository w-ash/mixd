import { Bookmark, Music } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router";
import { useGetConnectorsApiV1ConnectorsGet } from "#/api/generated/connectors/connectors";
import { useListTracksApiV1TracksGet } from "#/api/generated/tracks/tracks";
import { STALE } from "#/api/query-client";
import { PageHeader } from "#/components/layout/PageHeader";
import { ActiveFilterChips } from "#/components/library/ActiveFilterChips";
import {
  FilterPanelChevron,
  LibraryFilterPanel,
} from "#/components/library/LibraryFilterPanel";
import { SaveFiltersAsWorkflowDialog } from "#/components/library/SaveFiltersAsWorkflowDialog";
import { TrackTable } from "#/components/library/TrackTable";
import { BulkSelectionBar } from "#/components/shared/BulkSelectionBar";
import { BulkTagDialog } from "#/components/shared/BulkTagDialog";
import { EmptyState } from "#/components/shared/EmptyState";
import { QueryStates } from "#/components/shared/QueryStates";
import { ListRowsSkeleton } from "#/components/shared/skeletons";
import { TablePagination } from "#/components/shared/TablePagination";
import { Badge } from "#/components/ui/badge";
import { Button } from "#/components/ui/button";
import { Input } from "#/components/ui/input";
import type { SortDir, SortField } from "#/hooks/useLibraryFilters";
import { useLibraryFilters } from "#/hooks/useLibraryFilters";
import { usePagination } from "#/hooks/usePagination";
import { useSelectionSet } from "#/hooks/useSelectionSet";
import { isConnectable } from "#/lib/connectors";
import { formatCount, formatList } from "#/lib/format";
import { pluralSuffix } from "#/lib/pluralize";
import { cn } from "#/lib/utils";

const PAGE_SIZE = 50;

export function Library() {
  const cursorMapRef = useRef<Map<number, string>>(new Map());
  // The selection lives below the tracks query, but the filter-write callback
  // above it has to clear it — the ref bridges the two without re-running.
  const clearSelectionRef = useRef<(() => void) | null>(null);
  const [bulkTagOpen, setBulkTagOpen] = useState(false);
  const [saveWorkflowOpen, setSaveWorkflowOpen] = useState(false);

  // Every filter write clears the cursor cache and the selection, so the user
  // can't silently bulk-tag tracks they can no longer see.
  const resetLocalState = useCallback(() => {
    cursorMapRef.current.clear();
    clearSelectionRef.current?.();
  }, []);
  const {
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
  } = useLibraryFilters({ onMutate: resetLocalState });

  // Pagination — offset derived from URL ?page= before the query fires;
  // usePagination runs after it for totalPages/setPage (both need `total`).
  const pageParam = Number(searchParams.get("page") ?? "1");
  const queryOffset = (pageParam - 1) * PAGE_SIZE;
  // Keyset pagination: cache cursors from API responses for sequential nav.
  // Map: page number → cursor for the *next* page after that page.
  const cursorForPage = cursorMapRef.current.get(pageParam - 1);

  // Auto-open when filters become active — from a chip or the URL as much as
  // from the panel itself — and never auto-close: only the user closes it.
  // Adjusted during render rather than in an effect, so the panel opens in the
  // same pass the count grows in, and an unrelated URL write (a search
  // keystroke, a sort) leaves a user's collapse alone.
  const [filterPanelOpen, setFilterPanelOpen] = useState(() => activeCount > 0);
  const [prevActiveCount, setPrevActiveCount] = useState(activeCount);
  if (activeCount !== prevActiveCount) {
    setPrevActiveCount(activeCount);
    if (activeCount > prevActiveCount) setFilterPanelOpen(true);
  }

  const { data, isLoading, isError, error, isPlaceholderData } =
    useListTracksApiV1TracksGet(
      {
        ...toQueryParams(),
        limit: PAGE_SIZE,
        offset: queryOffset,
        // Only pay for GROUP BYs when the user is looking at the filters.
        include_facets: filterPanelOpen,
        ...(cursorForPage ? { cursor: cursorForPage } : {}),
      },
      { query: { staleTime: 30_000, placeholderData: (prev) => prev } },
    );

  const response = data?.status === 200 ? data.data : undefined;
  const tracks = response?.data ?? [];
  const total = response?.total ?? 0;
  const facets = response?.facets ?? null;

  const trackIds = useMemo(() => tracks.map((t) => t.id), [tracks]);
  const selection = useSelectionSet(trackIds);
  clearSelectionRef.current = selection.clear;

  // Cache the next_cursor from the latest response
  const nextCursor = response?.next_cursor;
  useEffect(() => {
    if (nextCursor) {
      cursorMapRef.current.set(pageParam, nextCursor);
    }
  }, [nextCursor, pageParam]);

  const { page, totalPages, setPage } = usePagination(total);

  // Connectors list for filter dropdown
  const { data: connectorsData } = useGetConnectorsApiV1ConnectorsGet({
    query: { staleTime: STALE.STATIC },
  });
  const connectors = connectorsData?.status === 200 ? connectorsData.data : [];

  const handleSort = useCallback(
    (field: SortField, dir: SortDir) => setFilter("sort", { field, dir }),
    [setFilter],
  );

  const hasFilters = activeCount > 0 || Boolean(filters.search);

  return (
    <div>
      <title>Library — Mixd</title>
      <PageHeader
        title="Library"
        description={
          total > 0
            ? `${formatCount(total)} track${pluralSuffix(total)} across all services.`
            : "Your complete track collection."
        }
      />
      {/* Announces result-count changes to screen readers as filters are
          applied or cleared. WCAG 2.2 "status messages" guidance. */}
      <span className="sr-only" aria-live="polite">
        {total > 0
          ? `${formatCount(total)} track${pluralSuffix(total)} match${total === 1 ? "es" : ""} current filters.`
          : "No tracks match current filters."}
      </span>

      {/* Compact toolbar: search + Filters toggle + Save as Workflow */}
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-48">
          <Input
            type="search"
            placeholder="Search tracks, artists, albums..."
            value={searchInput}
            onChange={(e) => setFilter("search", e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape" && searchInput !== "") {
                e.preventDefault();
                setFilter("search", null);
              }
            }}
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

        <Button
          type="button"
          variant="outline"
          onClick={() => setFilterPanelOpen(!filterPanelOpen)}
          aria-expanded={filterPanelOpen}
          aria-controls="library-filter-panel"
          className="gap-2"
        >
          <span>Filters</span>
          {activeCount > 0 && (
            <Badge variant="default" className="min-w-5 px-1.5 py-0">
              {activeCount}
              <span className="sr-only">
                {" "}
                active filter{pluralSuffix(activeCount)}
              </span>
            </Badge>
          )}
          <FilterPanelChevron expanded={filterPanelOpen} />
        </Button>

        <Button
          type="button"
          variant="outline"
          disabled={mappableCount === 0}
          onClick={() => setSaveWorkflowOpen(true)}
          title={
            mappableCount === 0
              ? "Apply a preference, liked, source, or tag filter first"
              : "Save the current filters as a reusable workflow"
          }
          className="gap-2"
        >
          <Bookmark className="size-3.5" />
          Save as Workflow
        </Button>
      </div>

      <LibraryFilterPanel
        expanded={filterPanelOpen}
        onClose={() => setFilterPanelOpen(false)}
        filters={filters}
        setFilter={setFilter}
        setFilters={setFilters}
        connectors={connectors}
        facets={facets}
      />

      <ActiveFilterChips
        filters={filters}
        setFilter={setFilter}
        onClearAll={clear}
      />

      {/* Mounted only while open so the form starts empty every time. */}
      {saveWorkflowOpen && (
        <SaveFiltersAsWorkflowDialog
          open
          onOpenChange={setSaveWorkflowOpen}
          filters={{
            preference: filters.preference,
            tags: filters.tags,
            tagMode: filters.tagMode,
            liked: filters.liked === null ? null : filters.liked === "true",
            connector: filters.connector,
          }}
          narrowsToLiked={!filters.preference && filters.liked !== "true"}
        />
      )}

      <BulkSelectionBar count={selection.size} onClear={selection.clear}>
        <Button size="sm" onClick={() => setBulkTagOpen(true)}>
          Tag selected
        </Button>
      </BulkSelectionBar>

      <QueryStates
        loading={isLoading}
        isError={isError}
        error={error}
        errorHeading="Failed to load tracks"
        skeleton={
          <ListRowsSkeleton
            rows={8}
            bars={["h-5 w-56", "h-5 w-32", "h-5 w-16"]}
          />
        }
        isEmpty={tracks.length === 0}
        empty={
          <EmptyState
            icon={<Music className="size-10" />}
            heading={hasFilters ? "No matching tracks" : "No tracks yet"}
            description={(() => {
              if (hasFilters) return "Try adjusting your search or filters.";
              const connectableLabels = connectors
                .filter((c) => isConnectable(c.auth_method))
                .map((c) => c.display_name);
              const source =
                connectableLabels.length > 0
                  ? formatList(connectableLabels, "disjunction")
                  : "a music service";
              return `Import your music from ${source} to see your library here.`;
            })()}
            action={
              !hasFilters ? (
                <Button size="sm" asChild>
                  <Link to="/settings/sync">Import Music</Link>
                </Button>
              ) : undefined
            }
          />
        }
      >
        {/* Track results — table on wide containers, cards on narrow */}
        <div
          className={cn(
            "transition-all duration-200",
            isPlaceholderData && "opacity-70 blur-[0.5px]",
          )}
        >
          <TrackTable
            tracks={tracks}
            selection={selection}
            sort={{
              field: filters.sort.field,
              dir: filters.sort.dir,
              onSort: handleSort,
            }}
          />

          <TablePagination
            page={page}
            totalPages={totalPages}
            total={total}
            limit={PAGE_SIZE}
            onPageChange={(nextPage) => {
              selection.clear();
              setPage(nextPage);
            }}
          />
        </div>
      </QueryStates>

      <BulkTagDialog
        open={bulkTagOpen}
        onOpenChange={setBulkTagOpen}
        trackIds={Array.from(selection.selected)}
        onTagged={selection.clear}
      />
    </div>
  );
}
