import { Heart } from "lucide-react";
import { Link } from "react-router";

import type { LibraryTrackSchema } from "#/api/generated/model";
import { ArtistCredits } from "#/components/shared/ArtistCredits";
import { ConnectorIcon } from "#/components/shared/ConnectorIcon";
import { PreferenceBadge } from "#/components/shared/PreferenceToggle";
import { ResponsiveTable } from "#/components/shared/ResponsiveTable";
import type { TableSort } from "#/components/shared/SortableHead";
import { SortableHead } from "#/components/shared/SortableHead";
import { TableCard } from "#/components/shared/TableCard";
import { TagChip } from "#/components/shared/TagChip";
import { TitleLink } from "#/components/shared/TitleLink";
import {
  RowHoverAccent,
  rowStaggerStyle,
} from "#/components/shared/table-row-motion";
import { Checkbox } from "#/components/ui/checkbox";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "#/components/ui/table";
import type { SortField } from "#/hooks/useLibraryFilters";
import { SORT_LABELS } from "#/hooks/useLibraryFilters";
import type { SelectionSet } from "#/hooks/useSelectionSet";
import { formatCount, formatDuration, formatRelativeTime } from "#/lib/format";
import { pluralSuffix } from "#/lib/pluralize";

const TAGS_PREVIEW_CAP = 3;

/** Inline tag list for a track row — shows first few chips, then "+N". */
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
  onSelectedChange?: (next: boolean) => void;
}

/**
 * Card representation of a track row — used by ResponsiveTable below the
 * @2xl container threshold (typically iPhone / iPad portrait widths).
 */
function TrackCard({ track, selected, onSelectedChange }: TrackCardProps) {
  return (
    <TableCard
      leading={
        onSelectedChange ? (
          <Checkbox
            aria-label={`Select ${track.title}`}
            checked={selected}
            onCheckedChange={(checked) => onSelectedChange(checked === true)}
            className="mt-1 shrink-0"
          />
        ) : undefined
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
        <ArtistCredits artists={track.artists} />
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

/** Sort state a caller owns, handed to the table so headers can drive it. */
export type TrackTableSort = Omit<TableSort<SortField>, "labels">;

interface TrackTableProps {
  tracks: LibraryTrackSchema[];
  /**
   * Bulk-selection state. Omitted on views with no bulk actions — the select
   * column and the per-row checkboxes disappear with it.
   */
  selection?: SelectionSet;
  /** Omitted where the caller owns no sort state; headers go plain. */
  sort?: TrackTableSort;
}

/**
 * Canonical track result list: a full column table on wide content areas, a
 * card list on narrow ones. Shared by the Library and by an artist's tracks so
 * a row reads the same wherever tracks are listed.
 */
export function TrackTable({ tracks, selection, sort }: TrackTableProps) {
  const headSort = sort ? { ...sort, labels: SORT_LABELS } : undefined;

  return (
    <ResponsiveTable
      cards={
        <div className="flex flex-col gap-2">
          {tracks.map((track) => (
            <TrackCard
              key={track.id}
              track={track}
              selected={selection?.isSelected(track.id) ?? false}
              onSelectedChange={
                selection
                  ? (checked) => selection.toggle(track.id, checked)
                  : undefined
              }
            />
          ))}
        </div>
      }
      table={
        <Table>
          <TableHeader>
            <TableRow>
              {selection && (
                <TableHead className="w-8">
                  <Checkbox
                    aria-label="Select all rows on this page"
                    checked={selection.headerChecked}
                    onCheckedChange={selection.toggleAll}
                  />
                </TableHead>
              )}
              <TableHead className="w-8">
                <span className="sr-only">Liked</span>
              </TableHead>
              <SortableHead field="title" sort={headSort}>
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
                sort={headSort}
                className="w-20 text-right"
              >
                Duration
              </SortableHead>
              <SortableHead
                field="plays"
                sort={headSort}
                className="w-16 text-right"
              >
                Plays
              </SortableHead>
              <SortableHead
                field="last_played"
                sort={headSort}
                className="w-28"
              >
                Last Played
              </SortableHead>
              <TableHead className="w-10 text-center">Pref</TableHead>
              <TableHead className="hidden w-48 2xl:table-cell">Tags</TableHead>
              <TableHead className="w-24 text-center">Sources</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {tracks.map((track, index) => (
              <TableRow
                key={track.id}
                className="group relative"
                style={rowStaggerStyle(index)}
              >
                {/* Select */}
                {selection && (
                  <TableCell className="w-8 text-center">
                    <Checkbox
                      aria-label={`Select ${track.title}`}
                      checked={selection.isSelected(track.id)}
                      onCheckedChange={(checked) =>
                        selection.toggle(track.id, checked === true)
                      }
                    />
                  </TableCell>
                )}
                {/* Liked */}
                <TableCell className="relative w-8 text-center">
                  <RowHoverAccent />
                  {track.is_liked && (
                    <Heart
                      className="mx-auto size-3.5 text-status-liked -translate-y-px"
                      aria-label="Liked"
                    />
                  )}
                </TableCell>
                {/* Title — truncated so one long title can't push the
                    play columns past the viewport edge. */}
                <TableCell className="max-w-96 truncate" title={track.title}>
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
                  <ArtistCredits artists={track.artists} />
                </TableCell>
                {/* Album */}
                <TableCell className="hidden max-w-48 truncate text-text-muted text-sm 2xl:table-cell">
                  {track.album ?? "—"}
                </TableCell>
                {/* Duration */}
                <TableCell className="text-right tabular-nums text-text-muted text-sm">
                  {formatDuration(track.duration_ms)}
                </TableCell>
                {/* Plays */}
                <TableCell className="text-right tabular-nums text-text-muted text-sm">
                  {track.total_plays ? formatCount(track.total_plays) : "—"}
                </TableCell>
                {/* Last Played */}
                <TableCell
                  className="whitespace-nowrap text-text-muted text-sm"
                  title={track.last_played ?? undefined}
                >
                  {track.last_played
                    ? formatRelativeTime(track.last_played)
                    : "—"}
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
  );
}
