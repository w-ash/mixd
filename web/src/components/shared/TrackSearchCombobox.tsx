import { Command } from "cmdk";
import { Search } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { LibraryTrackSchema } from "@/api/generated/model";
import { useListTracksApiV1TracksGet } from "@/api/generated/tracks/tracks";
import { ConnectorIcon } from "@/components/shared/ConnectorIcon";

interface TrackSearchComboboxProps {
  onSelect: (track: LibraryTrackSchema) => void;
  excludeTrackId?: number;
  placeholder?: string;
}

export function TrackSearchCombobox({
  onSelect,
  excludeTrackId,
  placeholder = "Search tracks...",
}: TrackSearchComboboxProps) {
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  // Debounce search input
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedQuery(query), 300);
    return () => clearTimeout(timer);
  }, [query]);

  const { data, isLoading } = useListTracksApiV1TracksGet(
    { q: debouncedQuery || undefined, limit: 10 },
    { query: { enabled: debouncedQuery.length >= 2 } },
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
    >
      <div className="flex items-center gap-2 border-b border-border-muted px-3">
        <Search className="size-4 text-text-muted" />
        <Command.Input
          ref={inputRef}
          value={query}
          onValueChange={setQuery}
          placeholder={placeholder}
          className="flex h-10 w-full bg-transparent text-sm text-text outline-none placeholder:text-text-faint"
        />
      </div>
      <Command.List className="max-h-60 overflow-y-auto p-1">
        {debouncedQuery.length < 2 && (
          <Command.Empty className="p-4 text-center text-sm text-text-muted">
            Type at least 2 characters to search.
          </Command.Empty>
        )}
        {debouncedQuery.length >= 2 && isLoading && (
          <Command.Loading className="p-4 text-center text-sm text-text-muted">
            Searching...
          </Command.Loading>
        )}
        {debouncedQuery.length >= 2 && !isLoading && tracks.length === 0 && (
          <Command.Empty className="p-4 text-center text-sm text-text-muted">
            No tracks found.
          </Command.Empty>
        )}
        {tracks.map((track) => (
          <Command.Item
            key={track.id}
            value={String(track.id)}
            onSelect={() => onSelect(track)}
            className="flex cursor-pointer items-center gap-3 rounded-md px-3 py-2 text-sm hover:bg-surface-sunken aria-selected:bg-surface-sunken"
          >
            <div className="min-w-0 flex-1">
              <div className="truncate font-medium text-text">
                {track.title}
              </div>
              <div className="truncate text-xs text-text-muted">
                {track.artists.map((a) => a.name).join(", ")}
                {track.album && ` — ${track.album}`}
              </div>
            </div>
            <div className="flex shrink-0 gap-1">
              {track.connector_names.map((name) => (
                <ConnectorIcon key={name} name={name} />
              ))}
            </div>
          </Command.Item>
        ))}
      </Command.List>
    </Command>
  );
}
