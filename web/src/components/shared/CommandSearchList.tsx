import { Command } from "cmdk";
import { Search } from "lucide-react";
import { type ReactNode, useEffect, useRef } from "react";

import type { LibraryTrackSchema } from "#/api/generated/model";
import { useListTracksApiV1TracksGet } from "#/api/generated/tracks/tracks";
import { TrackResultRow } from "#/components/shared/TrackResultRow";
import { useTrackSearch } from "#/hooks/useTrackSearch";

interface CommandSearchListProps {
  onSelect: (track: LibraryTrackSchema) => void;
  /** Result page size. Default 10; the multi-select add dialog passes 20. */
  limit?: number;
  /**
   * Extra gate ANDed with `deferredSearch.length >= 2` for the query's
   * `enabled`. Default true; consumers inside a dialog pass its `open` flag so
   * the search doesn't fire before the surface is visible.
   */
  enabled?: boolean;
  /** Post-fetch filter: drop this track id before the empty-state check. */
  excludeTrackId?: string;
  placeholder?: string;
  /** cmdk `loop` — wrap keyboard navigation at the list ends. */
  loop?: boolean;
  /** `Command.List` height/overflow classes. */
  listClassName?: string;
  /** Rendered before each row's text block (e.g. a selection checkbox). */
  rowLeading?: (track: LibraryTrackSchema) => ReactNode;
  /** Rendered after each row's text block (e.g. an "Added" badge). */
  rowTrailing?: (track: LibraryTrackSchema) => ReactNode;
}

/**
 * The `cmdk` track-search shell: input binding, min-2-chars threshold, the
 * 3-state list ladder (prompt / loading / empty), and a shared result row.
 * Owns its own search + query state so every picker debounces, thresholds,
 * and reads identically — the single source that killed the v0.8.11 fork.
 *
 * Focus-on-mount stands in for an explicit open effect: both consumers mount
 * this subtree exactly when their surface (dialog) opens and unmount it on
 * close, which also disposes the search state.
 */
export function CommandSearchList({
  onSelect,
  limit = 10,
  enabled = true,
  excludeTrackId,
  placeholder = "Search tracks...",
  loop = false,
  listClassName = "max-h-60 overflow-y-auto p-1",
  rowLeading,
  rowTrailing,
}: CommandSearchListProps) {
  const { search, setSearch, deferredSearch } = useTrackSearch();
  const inputRef = useRef<HTMLInputElement>(null);

  const { data, isLoading } = useListTracksApiV1TracksGet(
    { q: deferredSearch || undefined, limit },
    { query: { enabled: enabled && deferredSearch.length >= 2 } },
  );

  const tracks =
    data?.status === 200
      ? data.data.data.filter((t) => t.id !== excludeTrackId)
      : [];

  // Focus on mount
  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  return (
    <Command
      className="rounded-lg border border-border-muted bg-surface"
      shouldFilter={false}
      loop={loop}
    >
      <div className="flex items-center gap-2 border-b border-border-muted px-3">
        <Search className="size-4 text-text-muted" />
        <Command.Input
          ref={inputRef}
          value={search}
          onValueChange={setSearch}
          placeholder={placeholder}
          className="flex h-10 w-full bg-transparent text-sm text-text outline-none placeholder:text-text-faint"
        />
      </div>
      <Command.List className={listClassName}>
        {deferredSearch.length < 2 && (
          <Command.Empty className="p-4 text-center text-sm text-text-muted">
            Type at least 2 characters to search.
          </Command.Empty>
        )}
        {deferredSearch.length >= 2 && isLoading && (
          <Command.Loading className="p-4 text-center text-sm text-text-muted">
            Searching…
          </Command.Loading>
        )}
        {deferredSearch.length >= 2 && !isLoading && tracks.length === 0 && (
          <Command.Empty className="p-4 text-center text-sm text-text-muted">
            No tracks found.
          </Command.Empty>
        )}
        {tracks.map((track) => (
          <Command.Item
            key={track.id}
            value={track.id}
            onSelect={() => onSelect(track)}
            className="flex cursor-pointer items-center gap-3 rounded-md px-3 py-2 text-sm hover:bg-surface-sunken aria-selected:bg-surface-sunken"
          >
            <TrackResultRow
              track={track}
              leading={rowLeading?.(track)}
              trailing={rowTrailing?.(track)}
            />
          </Command.Item>
        ))}
      </Command.List>
    </Command>
  );
}
