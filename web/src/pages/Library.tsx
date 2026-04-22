import { ArrowUp, Bookmark, Heart, Music, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router";
import { useGetConnectorsApiV1ConnectorsGet } from "#/api/generated/connectors/connectors";
import { useListTracksApiV1TracksGet } from "#/api/generated/tracks/tracks";
import { STALE } from "#/api/query-client";
import { PageHeader } from "#/components/layout/PageHeader";
import { ActiveFilterChips } from "#/components/library/ActiveFilterChips";
import {
  countActiveFilters,
  FilterPanelChevron,
  LibraryFilterPanel,
} from "#/components/library/LibraryFilterPanel";
import { SaveFiltersAsWorkflowDialog } from "#/components/library/SaveFiltersAsWorkflowDialog";
import { BulkTagDialog } from "#/components/shared/BulkTagDialog";
import { ConnectorIcon } from "#/components/shared/ConnectorIcon";
import { EmptyState } from "#/components/shared/EmptyState";
import { PreferenceBadge } from "#/components/shared/PreferenceToggle";
import { QueryErrorState } from "#/components/shared/QueryErrorState";
import { TablePagination } from "#/components/shared/TablePagination";
import { TagChip } from "#/components/shared/TagChip";
import { Badge } from "#/components/ui/badge";
import { Button } from "#/components/ui/button";
import { Checkbox } from "#/components/ui/checkbox";
import { Input } from "#/components/ui/input";
import { Skeleton } from "#/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "#/components/ui/table";
import { useFilterState } from "#/hooks/useFilterState";
import { usePagination } from "#/hooks/usePagination";
import { useTrackSearch } from "#/hooks/useTrackSearch";
import { parsePreferenceParam } from "#/lib/filters-to-workflow";
import { formatArtists, formatDuration, formatList } from "#/lib/format";
import { pluralSuffix } from "#/lib/pluralize";
import { cn } from "#/lib/utils";

const PAGE_SIZE = 50;
const STAGGER_CAP = 15;
const TAGS_PREVIEW_CAP = 3;

/** Inline tag list for a Library row — shows first few chips, then "+N". */
function TagRowChips({ tags }: { tags: string[] }) {
  const visible = tags.slice(0, TAGS_PREVIEW_CAP);
  const overflow = tags.length - visible.length;
  return (
    <div className="flex flex-wrap items-center gap-1">
      {visible.map((tag) => (
        <TagChip key={tag} tag={tag} />
      ))}
      {overflow > 0 && (
        <span className="font-mono text-xs text-text-muted">+{overflow}</span>
      )}
    </div>
  );
}

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
  const cursorMapRef = useRef<Map<number, string>>(new Map());
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

  // Shared URL-filter mutations — clears cursor cache + selection on every
  // write so the user can't silently bulk-tag tracks they can no longer see.
  const resetLocalState = useCallback(() => {
    cursorMapRef.current.clear();
    setSelectedIds(new Set());
  }, []);
  const { searchParams, setFilter, setMultiFilter, clearAll } = useFilterState({
    onMutate: resetLocalState,
  });
  const { search, setSearch, deferredSearch, isSearching } = useTrackSearch(
    searchParams.get("q") ?? "",
  );

  // URL-driven filter state
  const likedParam = searchParams.get("liked");
  const connectorParam = searchParams.get("connector");
  // Validate preference param so ?preference=garbage doesn't flow downstream.
  const preferenceParam = parsePreferenceParam(searchParams.get("preference"));
  const tagParams = searchParams.getAll("tag");
  const tagModeParam: "and" | "or" =
    searchParams.get("tag_mode") === "or" ? "or" : "and";
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
  // (cursorMapRef + selectedIds are declared at the top of the function so
  //  useFilterState's onMutate callback can reset them.)

  const [bulkTagOpen, setBulkTagOpen] = useState(false);

  // Filter panel + save-as-workflow UI state. Panel auto-opens whenever
  // filters become active (via URL nav, chip dismiss, or panel interaction)
  // and stays open once the user has engaged with it — we never auto-close
  // it on filter clear, so the user can keep editing without losing their
  // place. Manual toggle via the toolbar button still works.
  const activeFilterCount = useMemo(
    () =>
      countActiveFilters({
        preference: preferenceParam,
        liked: likedParam,
        connector: connectorParam,
        tags: tagParams,
      }),
    [preferenceParam, likedParam, connectorParam, tagParams],
  );
  const [filterPanelOpen, setFilterPanelOpen] = useState(activeFilterCount > 0);
  useEffect(() => {
    if (activeFilterCount > 0) setFilterPanelOpen(true);
  }, [activeFilterCount]);
  const [saveWorkflowOpen, setSaveWorkflowOpen] = useState(false);

  // Use cursor if available from the previous page (sequential next-page)
  const cursorForPage = cursorMapRef.current.get(pageParam - 1);

  const { data, isLoading, isError, error, isPlaceholderData } =
    useListTracksApiV1TracksGet(
      {
        q: querySearch,
        liked: likedFilter,
        connector: connectorParam ?? undefined,
        preference: preferenceParam ?? undefined,
        tag: tagParams.length > 0 ? tagParams : undefined,
        tag_mode: tagModeParam,
        sort: sortParam,
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

  const setTagFilters = useCallback(
    (tags: string[]) => setMultiFilter("tag", tags),
    [setMultiFilter],
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
    setFilter("q", value);
  };

  const hasFilters =
    querySearch ||
    likedParam ||
    connectorParam ||
    preferenceParam ||
    tagParams.length > 0;

  return (
    <div>
      <title>Library — Mixd</title>
      <PageHeader
        title="Library"
        description={
          total > 0
            ? `${total.toLocaleString()} track${pluralSuffix(total)} across all services.`
            : "Your complete track collection."
        }
      />
      {/* Announces result-count changes to screen readers as filters are
          applied or cleared. WCAG 2.2 "status messages" guidance. */}
      <span className="sr-only" aria-live="polite">
        {total > 0
          ? `${total.toLocaleString()} track${pluralSuffix(total)} match${total === 1 ? "es" : ""} current filters.`
          : "No tracks match current filters."}
      </span>

      {/* Compact toolbar: search + Filters toggle + Save as Workflow */}
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-48">
          <Input
            type="search"
            placeholder="Search tracks, artists, albums..."
            value={search}
            onChange={handleSearchChange}
            onKeyDown={(e) => {
              if (e.key === "Escape" && search !== "") {
                e.preventDefault();
                setSearch("");
                setFilter("q", null);
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
          onClick={() => setFilterPanelOpen((v) => !v)}
          aria-expanded={filterPanelOpen}
          aria-controls="library-filter-panel"
          className="gap-2"
        >
          <span>Filters</span>
          {activeFilterCount > 0 && (
            <Badge variant="default" className="min-w-5 px-1.5 py-0">
              {activeFilterCount}
              <span className="sr-only">
                {" "}
                active filter{pluralSuffix(activeFilterCount)}
              </span>
            </Badge>
          )}
          <FilterPanelChevron expanded={filterPanelOpen} />
        </Button>

        <Button
          type="button"
          variant="outline"
          disabled={activeFilterCount === 0}
          onClick={() => setSaveWorkflowOpen(true)}
          title={
            activeFilterCount === 0
              ? "Apply a filter first to save as workflow"
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
        preference={preferenceParam}
        liked={
          likedParam === "true" || likedParam === "false" ? likedParam : null
        }
        connector={connectorParam}
        tags={tagParams}
        tagMode={tagModeParam}
        connectors={connectors}
        facets={facets}
        onPreferenceChange={(value) => setFilter("preference", value)}
        onLikedChange={(value) => setFilter("liked", value)}
        onConnectorChange={(value) => setFilter("connector", value)}
        onTagsChange={setTagFilters}
        onTagModeChange={(mode) =>
          setFilter("tag_mode", mode === "and" ? null : mode)
        }
      />

      <ActiveFilterChips
        search={querySearch ?? null}
        liked={likedParam}
        connector={connectorParam}
        preference={preferenceParam}
        tags={tagParams}
        onClearFilter={(key) => {
          if (key === "q") {
            setSearch("");
            setFilter("q", null);
          } else {
            setFilter(key, null);
          }
        }}
        onRemoveTag={(tag) => setTagFilters(tagParams.filter((t) => t !== tag))}
        onClearAll={() => {
          setSearch("");
          clearAll();
        }}
      />

      <SaveFiltersAsWorkflowDialog
        open={saveWorkflowOpen}
        onOpenChange={setSaveWorkflowOpen}
        filters={{
          preference: preferenceParam,
          tags: tagParams,
          tagMode: tagModeParam,
          liked: likedFilter ?? null,
          connector: connectorParam,
        }}
        narrowsToLiked={!preferenceParam && likedFilter !== true}
      />

      {/* Bulk-action toolbar — only visible while a selection exists. */}
      {selectedIds.size > 0 && (
        <section
          aria-label="Bulk selection"
          className="mb-3 flex items-center gap-3 rounded-md border border-primary/40 bg-primary/5 px-3 py-2 text-sm"
        >
          <span className="font-display text-text">
            {selectedIds.size} selected
          </span>
          <Button size="sm" onClick={() => setBulkTagOpen(true)}>
            Tag selected
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setSelectedIds(new Set())}
            aria-label="Clear selection"
          >
            <X className="mr-1 size-3.5" />
            Clear
          </Button>
        </section>
      )}

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
          description={(() => {
            if (hasFilters) return "Try adjusting your search or filters.";
            const oauthLabels = connectors
              .filter((c) => c.auth_method === "oauth")
              .map((c) => c.display_name);
            const source =
              oauthLabels.length > 0
                ? formatList(oauthLabels, "disjunction")
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
                  <Checkbox
                    aria-label="Select all rows on this page"
                    checked={
                      tracks.length === 0
                        ? false
                        : tracks.every((t) => selectedIds.has(t.id))
                          ? true
                          : tracks.some((t) => selectedIds.has(t.id))
                            ? "indeterminate"
                            : false
                    }
                    onCheckedChange={(checked) => {
                      if (checked) {
                        setSelectedIds(new Set(tracks.map((t) => t.id)));
                      } else {
                        setSelectedIds(new Set());
                      }
                    }}
                  />
                </TableHead>
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
                <TableHead className="w-10 text-center">Pref</TableHead>
                <TableHead className="w-48">Tags</TableHead>
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
                  {/* Select */}
                  <TableCell className="w-8 text-center">
                    <Checkbox
                      aria-label={`Select ${track.title}`}
                      checked={selectedIds.has(track.id)}
                      onCheckedChange={(checked) => {
                        setSelectedIds((prev) => {
                          const next = new Set(prev);
                          if (checked) next.add(track.id);
                          else next.delete(track.id);
                          return next;
                        });
                      }}
                    />
                  </TableCell>
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
                  {/* Preference */}
                  <TableCell className="w-10 text-center">
                    {track.preference && (
                      <PreferenceBadge state={track.preference} />
                    )}
                  </TableCell>
                  {/* Tags */}
                  <TableCell className="w-48">
                    {track.tags && track.tags.length > 0 && (
                      <TagRowChips tags={track.tags} />
                    )}
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
            onPageChange={(nextPage) => {
              setSelectedIds(new Set());
              setPage(nextPage);
            }}
          />
        </div>
      )}

      <BulkTagDialog
        open={bulkTagOpen}
        onOpenChange={setBulkTagOpen}
        trackIds={Array.from(selectedIds)}
        onTagged={() => setSelectedIds(new Set())}
      />
    </div>
  );
}
