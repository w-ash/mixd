import { Users } from "lucide-react";
import { useCallback } from "react";
import { Link } from "react-router";

import { useListArtistsApiV1ArtistsGet } from "#/api/generated/artists/artists";
// The generated model barrel does not yet re-export the artist schemas, so
// this comes straight from its generated module.
import type { ArtistSummarySchema } from "#/api/generated/model/artistSummarySchema";
import { PageHeader } from "#/components/layout/PageHeader";
import { ConnectorIcon } from "#/components/shared/ConnectorIcon";
import { EmptyState } from "#/components/shared/EmptyState";
import { FavoriteToggle } from "#/components/shared/FavoriteToggle";
import { QueryStates } from "#/components/shared/QueryStates";
import { ResponsiveTable } from "#/components/shared/ResponsiveTable";
import { SortableHead } from "#/components/shared/SortableHead";
import { ListRowsSkeleton } from "#/components/shared/skeletons";
import { TableCard } from "#/components/shared/TableCard";
import { TablePagination } from "#/components/shared/TablePagination";
import { TitleLink } from "#/components/shared/TitleLink";
import {
  RowHoverAccent,
  rowStaggerStyle,
} from "#/components/shared/table-row-motion";
import { Button } from "#/components/ui/button";
import { Input } from "#/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "#/components/ui/table";
import type { SortDirection } from "#/hooks/sort-param";
import { useArtistFavorite } from "#/hooks/useArtistFavorite";
import type { ArtistSortField } from "#/hooks/useArtistFilters";
import { ARTIST_SORT_LABELS, useArtistFilters } from "#/hooks/useArtistFilters";
import { useKeysetPagination } from "#/hooks/usePagination";
import { formatCount } from "#/lib/format";
import { pluralSuffix } from "#/lib/pluralize";
import { cn } from "#/lib/utils";

const PAGE_SIZE = 50;

/* ── Rows ────────────────────────────────────────────────── */

interface ArtistRowProps {
  artist: ArtistSummarySchema;
  onToggleFavorite: (artist: ArtistSummarySchema) => void;
}

/**
 * Card representation of an Artists row — used by ResponsiveTable below the
 * @2xl container threshold (typically iPhone / iPad portrait widths).
 */
function ArtistCard({ artist, onToggleFavorite }: ArtistRowProps) {
  return (
    <TableCard
      trailing={
        <FavoriteToggle
          isFavorited={artist.is_favorited === true}
          onToggle={() => onToggleFavorite(artist)}
          size="sm"
          label={artist.name}
        />
      }
    >
      <TitleLink to={`/artists/${artist.id}`} viewTransition>
        {artist.name}
      </TitleLink>
      <div className="mt-1.5 flex items-center gap-3 text-xs text-text-muted">
        <span className="shrink-0 tabular-nums">
          {formatCount(artist.track_count ?? 0)} track
          {pluralSuffix(artist.track_count ?? 0)}
        </span>
        {artist.kind && <span className="capitalize">{artist.kind}</span>}
      </div>
      {artist.connectors && artist.connectors.length > 0 && (
        <div className="mt-2 flex gap-1">
          {artist.connectors.map((name) => (
            <ConnectorIcon key={name} name={name} labelHidden />
          ))}
        </div>
      )}
    </TableCard>
  );
}

/** Two-option scope switch: the whole library, or just the favorites. */
function ScopeToggle({
  favorites,
  onChange,
}: {
  favorites: boolean;
  onChange: (favorites: boolean) => void;
}) {
  const options: { value: boolean; label: string }[] = [
    { value: false, label: "All" },
    { value: true, label: "Favorites" },
  ];

  return (
    <fieldset className="inline-flex items-center gap-1 rounded-lg border border-border-muted bg-surface-sunken p-1">
      <legend className="sr-only">Artist scope</legend>
      {options.map((option) => {
        const isActive = favorites === option.value;
        return (
          <button
            key={option.label}
            type="button"
            aria-pressed={isActive}
            onClick={() => onChange(option.value)}
            className={cn(
              "rounded-md px-3 py-1.5 font-display text-sm transition-colors",
              "focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none",
              isActive
                ? "bg-surface-elevated text-primary"
                : "text-text-muted hover:text-text",
            )}
          >
            {option.label}
          </button>
        );
      })}
    </fieldset>
  );
}

/* ── Page ────────────────────────────────────────────────── */

export function Artists() {
  const {
    page,
    limit,
    offset,
    cursor,
    totalPages,
    setPage,
    rememberNextCursor,
    resetCursors,
  } = useKeysetPagination({ defaultLimit: PAGE_SIZE });

  const { filters, setFilter, searchInput, isSearching, toQueryParams } =
    useArtistFilters({ onMutate: resetCursors });

  const { data, isLoading, isError, error, isPlaceholderData } =
    useListArtistsApiV1ArtistsGet(
      {
        ...toQueryParams(),
        limit,
        offset,
        ...(cursor ? { cursor } : {}),
      },
      { query: { staleTime: 30_000, placeholderData: (prev) => prev } },
    );

  const response = data?.status === 200 ? data.data : undefined;
  const artists = response?.data ?? [];
  const total = response?.total ?? 0;
  rememberNextCursor(response);

  const { toggle } = useArtistFavorite();
  const handleToggleFavorite = useCallback(
    (artist: ArtistSummarySchema) =>
      toggle(artist.id, artist.is_favorited === true),
    [toggle],
  );

  const handleSort = useCallback(
    (field: ArtistSortField, dir: SortDirection) =>
      setFilter("sort", { field, dir }),
    [setFilter],
  );

  const hasSearch = Boolean(filters.search);
  const headSort = {
    field: filters.sort.field,
    dir: filters.sort.dir,
    labels: ARTIST_SORT_LABELS,
    onSort: handleSort,
  };

  return (
    <div>
      <title>Artists — Mixd</title>
      <PageHeader
        title="Artists"
        description={
          total > 0
            ? `${formatCount(total)} artist${pluralSuffix(total)} across all services.`
            : "Everyone who plays on a track in your library."
        }
      />
      {/* Announces result-count changes to screen readers as filters are
          applied or cleared. WCAG 2.2 "status messages" guidance. */}
      <span className="sr-only" aria-live="polite">
        {total > 0
          ? `${formatCount(total)} artist${pluralSuffix(total)} match${total === 1 ? "es" : ""} current filters.`
          : "No artists match current filters."}
      </span>

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-48">
          <Input
            type="search"
            placeholder="Search artists..."
            value={searchInput}
            onChange={(e) => setFilter("search", e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape" && searchInput !== "") {
                e.preventDefault();
                setFilter("search", null);
              }
            }}
            aria-label="Search artists"
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

        <ScopeToggle
          favorites={filters.favorites}
          onChange={(next) => setFilter("favorites", next)}
        />
      </div>

      <QueryStates
        loading={isLoading}
        isError={isError}
        error={error}
        errorHeading="Failed to load artists"
        skeleton={
          <ListRowsSkeleton
            rows={8}
            bars={["h-5 w-56", "h-5 w-16", "h-5 w-16"]}
          />
        }
        isEmpty={artists.length === 0}
        empty={
          filters.favorites ? (
            <EmptyState
              icon={<Users className="size-10" />}
              heading="No favorite artists yet"
              description="Tap the heart on any artist to keep them one click away."
              action={
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => setFilter("favorites", false)}
                >
                  Show all artists
                </Button>
              }
            />
          ) : (
            <EmptyState
              icon={<Users className="size-10" />}
              heading={hasSearch ? "No matching artists" : "No artists yet"}
              description={
                hasSearch
                  ? "Try a different name or clear the search."
                  : "Import your music to see everyone who plays on it."
              }
              action={
                hasSearch ? undefined : (
                  <Button size="sm" asChild>
                    <Link to="/settings/sync">Import Music</Link>
                  </Button>
                )
              }
            />
          )
        }
      >
        <div
          className={cn(
            "transition-all duration-200",
            isPlaceholderData && "opacity-70 blur-[0.5px]",
          )}
        >
          <ResponsiveTable
            cards={
              <div className="flex flex-col gap-2">
                {artists.map((artist) => (
                  <ArtistCard
                    key={artist.id}
                    artist={artist}
                    onToggleFavorite={handleToggleFavorite}
                  />
                ))}
              </div>
            }
            table={
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-10">
                      <span className="sr-only">Favorite</span>
                    </TableHead>
                    <SortableHead field="name" sort={headSort}>
                      Name
                    </SortableHead>
                    <SortableHead
                      field="track_count"
                      sort={headSort}
                      className="w-24 text-right"
                    >
                      Tracks
                    </SortableHead>
                    <TableHead className="w-24 text-center">Sources</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {artists.map((artist, index) => (
                    <TableRow
                      key={artist.id}
                      className="group relative"
                      style={rowStaggerStyle(index)}
                    >
                      {/* Favorite */}
                      <TableCell className="relative w-10 text-center">
                        <RowHoverAccent />
                        <FavoriteToggle
                          isFavorited={artist.is_favorited === true}
                          onToggle={() => handleToggleFavorite(artist)}
                          size="sm"
                          label={artist.name}
                        />
                      </TableCell>
                      {/* Name */}
                      <TableCell
                        className="max-w-96 truncate"
                        title={artist.name}
                      >
                        <Link
                          to={`/artists/${artist.id}`}
                          viewTransition
                          className="font-medium text-text hover:text-primary transition-colors"
                        >
                          {artist.name}
                        </Link>
                      </TableCell>
                      {/* Tracks */}
                      <TableCell className="text-right tabular-nums text-text-muted text-sm">
                        {formatCount(artist.track_count ?? 0)}
                      </TableCell>
                      {/* Sources */}
                      <TableCell className="w-24">
                        <span className="flex justify-center gap-1">
                          {(artist.connectors ?? []).map((name) => (
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
            limit={limit}
            onPageChange={setPage}
          />
        </div>
      </QueryStates>
    </div>
  );
}
