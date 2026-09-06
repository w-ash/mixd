import { ArrowUp, Bookmark, Heart, Music } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router";
import { useGetConnectorsApiV1ConnectorsGet } from "#/api/generated/connectors/connectors";
import type { LibraryTrackSchema } from "#/api/generated/model";
import { useListTracksApiV1TracksGet } from "#/api/generated/tracks/tracks";
import { STALE } from "#/api/query-client";
import { PageHeader } from "#/components/layout/PageHeader";
import { ActiveFilterChips } from "#/components/library/ActiveFilterChips";
import {
  FilterPanelChevron,
  LibraryFilterPanel,
} from "#/components/library/LibraryFilterPanel";
import { SaveFiltersAsWorkflowDialog } from "#/components/library/SaveFiltersAsWorkflowDialog";
import { BulkSelectionBar } from "#/components/shared/BulkSelectionBar";
import { BulkTagDialog } from "#/components/shared/BulkTagDialog";
import { ConnectorIcon } from "#/components/shared/ConnectorIcon";
import { EmptyState } from "#/components/shared/EmptyState";
import { PreferenceBadge } from "#/components/shared/PreferenceToggle";
import { QueryStates } from "#/components/shared/QueryStates";
import { ResponsiveTable } from "#/components/shared/ResponsiveTable";
import { ListRowsSkeleton } from "#/components/shared/skeletons";
import { TableCard } from "#/components/shared/TableCard";
import { TablePagination } from "#/components/shared/TablePagination";
import { TagChip } from "#/components/shared/TagChip";
import { TitleLink } from "#/components/shared/TitleLink";
import { Badge } from "#/components/ui/badge";
import { Button } from "#/components/ui/button";
import { Checkbox } from "#/components/ui/checkbox";
import { Input } from "#/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "#/components/ui/table";
import type { SortDir, SortField } from "#/hooks/useLibraryFilters";
import { SORT_LABELS, useLibraryFilters } from "#/hooks/useLibraryFilters";
import { usePagination } from "#/hooks/usePagination";
import { useSelectionSet } from "#/hooks/useSelectionSet";
import { isConnectable } from "#/lib/connectors";
import {
  formatArtists,
  formatCount,
  formatDuration,
  formatList,
  formatRelativeTime,
} from "#/lib/format";
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

interface TrackCardProps {
  track: LibraryTrackSchema;
  selected: boolean;
  onSelectedChange: (next: boolean) => void;
}

/**
 * Card representation of a Library row — used by ResponsiveTable below the
 * @2xl container threshold (typically iPhone / iPad portrait widths).
 */
function TrackCard({ track, selected, onSelectedChange }: TrackCardProps) {
  return (
    <TableCard
      leading={
        <Checkbox
          aria-label={`Select ${track.title}`}
          checked={selected}
          onCheckedChange={(checked) => onSelectedChange(checked === true)}
          className="mt-1 shrink-0"
        />
      }
    >
      <div className="flex items-baseline justify-between gap-2">
        <TitleLink to={`/library/${track.id}`} viewTransition>
          {track.title}
        </TitleLink>
        {track.is_liked && (
          <Heart
            className="size-3.5 shrink-0 text-status-liked"
            aria-label="Liked"
          />
        )}
      </div>
      <p className="truncate text-sm text-text-muted">
        {formatArtists(track.artists)}
      </p>
      <div className="mt-1.5 flex items-center gap-3 text-xs text-text-muted">
        {track.album && <span className="truncate">{track.album}</span>}
        <span className="shrink-0 tabular-nums">
          {formatDuration(track.duration_ms)}
        </span>
        {track.total_plays ? (
          <span
            className="shrink-0 tabular-nums"
            title={track.last_played ?? undefined}
          >
            {formatCount(track.total_plays)} play
            {pluralSuffix(track.total_plays)}
          </span>
        ) : null}
        {track.preference && <PreferenceBadge state={track.preference} />}
      </div>
      {track.tags && track.tags.length > 0 && (
        <div className="mt-2">
          <TagRowChips tags={track.tags} />
        </div>
      )}
      {track.connector_names.length > 0 && (
        <div className="mt-2 flex gap-1">
          {track.connector_names.map((name) => (
            <ConnectorIcon key={name} name={name} labelHidden />
          ))}
        </div>
      )}
    </TableCard>
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
          <ResponsiveTable
            cards={
              <div className="flex flex-col gap-2">
                {tracks.map((track) => (
                  <TrackCard
                    key={track.id}
                    track={track}
                    selected={selection.isSelected(track.id)}
                    onSelectedChange={(checked) =>
                      selection.toggle(track.id, checked)
                    }
                  />
                ))}
              </div>
            }
            table={
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-8">
                      <Checkbox
                        aria-label="Select all rows on this page"
                        checked={selection.headerChecked}
                        onCheckedChange={selection.toggleAll}
                      />
                    </TableHead>
                    <TableHead className="w-8">
                      <span className="sr-only">Liked</span>
                    </TableHead>
                    <SortableHead
                      field="title"
                      currentField={filters.sort.field}
                      currentDir={filters.sort.dir}
                      onSort={handleSort}
                    >
                      Title
                    </SortableHead>
                    <TableHead>Artist</TableHead>
                    {/* Column priority: Album/Tags yield below 2xl so the
                        play columns (the default sort) stay in view without
                        horizontal scrolling. */}
                    <TableHead className="hidden w-48 2xl:table-cell">
                      Album
                    </TableHead>
                    <SortableHead
                      field="duration"
                      currentField={filters.sort.field}
                      currentDir={filters.sort.dir}
                      onSort={handleSort}
                      className="w-20 text-right"
                    >
                      Duration
                    </SortableHead>
                    <SortableHead
                      field="plays"
                      currentField={filters.sort.field}
                      currentDir={filters.sort.dir}
                      onSort={handleSort}
                      className="w-16 text-right"
                    >
                      Plays
                    </SortableHead>
                    <SortableHead
                      field="last_played"
                      currentField={filters.sort.field}
                      currentDir={filters.sort.dir}
                      onSort={handleSort}
                      className="w-28"
                    >
                      Last Played
                    </SortableHead>
                    <TableHead className="w-10 text-center">Pref</TableHead>
                    <TableHead className="hidden w-48 2xl:table-cell">
                      Tags
                    </TableHead>
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
                          checked={selection.isSelected(track.id)}
                          onCheckedChange={(checked) =>
                            selection.toggle(track.id, checked === true)
                          }
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
                      {/* Title — truncated so one long title can't push the
                          play columns past the viewport edge. */}
                      <TableCell
                        className="max-w-96 truncate"
                        title={track.title}
                      >
                        <Link
                          to={`/library/${track.id}`}
                          viewTransition
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
                      <TableCell className="hidden max-w-48 truncate text-text-muted text-sm 2xl:table-cell">
                        {track.album ?? "\u2014"}
                      </TableCell>
                      {/* Duration */}
                      <TableCell className="text-right tabular-nums text-text-muted text-sm">
                        {formatDuration(track.duration_ms)}
                      </TableCell>
                      {/* Plays */}
                      <TableCell className="text-right tabular-nums text-text-muted text-sm">
                        {track.total_plays
                          ? formatCount(track.total_plays)
                          : "\u2014"}
                      </TableCell>
                      {/* Last Played */}
                      <TableCell
                        className="whitespace-nowrap text-text-muted text-sm"
                        title={track.last_played ?? undefined}
                      >
                        {track.last_played
                          ? formatRelativeTime(track.last_played)
                          : "\u2014"}
                      </TableCell>
                      {/* Preference */}
                      <TableCell className="w-10 text-center">
                        {track.preference && (
                          <PreferenceBadge state={track.preference} />
                        )}
                      </TableCell>
                      {/* Tags */}
                      <TableCell className="hidden w-48 2xl:table-cell">
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
            }
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
