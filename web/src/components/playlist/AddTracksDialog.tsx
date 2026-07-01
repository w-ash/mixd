import { useQueryClient } from "@tanstack/react-query";
import { Command } from "cmdk";
import { Check, Plus, Search } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { LibraryTrackSchema } from "#/api/generated/model";
import {
  getGetPlaylistApiV1PlaylistsPlaylistIdGetQueryKey,
  getGetPlaylistTracksApiV1PlaylistsPlaylistIdTracksGetQueryKey,
  getListPlaylistsApiV1PlaylistsGetQueryKey,
  useAddPlaylistTracksApiV1PlaylistsPlaylistIdTracksPost,
} from "#/api/generated/playlists/playlists";
import { useListTracksApiV1TracksGet } from "#/api/generated/tracks/tracks";
import { ConnectorIcon } from "#/components/shared/ConnectorIcon";
import { Button } from "#/components/ui/button";
import {
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "#/components/ui/dialog";
import { ResponsiveDialog } from "#/components/ui/responsive-dialog";
import { useTrackSearch } from "#/hooks/useTrackSearch";
import { formatArtists } from "#/lib/format";
import { pluralize } from "#/lib/pluralize";
import { toasts } from "#/lib/toasts";

/**
 * Add-Tracks modal: catalog search with multi-select, then a single batch add.
 *
 * Follows the 2026 in-context add pattern (Apple Music's "Suggested Songs →
 * Add" lineage): search → check several → "Add N tracks", appended to the end.
 * Tracks already in the playlist show an "Added" badge but remain selectable —
 * manual add allows duplicates by design (flow 3.4).
 */
export function AddTracksDialog({
  playlistId,
  existingTrackIds,
}: {
  playlistId: string;
  existingTrackIds: Set<string>;
}) {
  const [open, setOpen] = useState(false);
  // Selection accumulates across searches (id → track), so a user can search,
  // pick a few, search again, and pick more before committing.
  const [selected, setSelected] = useState<Map<string, LibraryTrackSchema>>(
    new Map(),
  );
  const { search, setSearch, deferredSearch } = useTrackSearch();
  const queryClient = useQueryClient();
  const inputRef = useRef<HTMLInputElement>(null);

  // Focus the search input when the dialog opens (search-first modal) without
  // the `autoFocus` attribute the a11y lint flags.
  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  const { data, isLoading } = useListTracksApiV1TracksGet(
    { q: deferredSearch || undefined, limit: 20 },
    { query: { enabled: open && deferredSearch.length >= 2 } },
  );
  const results = data?.status === 200 ? data.data.data : [];

  const addMutation = useAddPlaylistTracksApiV1PlaylistsPlaylistIdTracksPost({
    mutation: {
      onSuccess: (_res, { data: body }) => {
        const n = body.track_ids.length;
        toasts.success(`${pluralize(n, "track")} added`);
        queryClient.invalidateQueries({
          queryKey:
            getGetPlaylistTracksApiV1PlaylistsPlaylistIdTracksGetQueryKey(
              playlistId,
            ),
        });
        queryClient.invalidateQueries({
          queryKey:
            getGetPlaylistApiV1PlaylistsPlaylistIdGetQueryKey(playlistId),
        });
        queryClient.invalidateQueries({
          queryKey: getListPlaylistsApiV1PlaylistsGetQueryKey(),
        });
        reset();
        setOpen(false);
      },
      meta: { errorLabel: "Failed to add tracks" },
    },
  });

  function reset() {
    setSelected(new Map());
    setSearch("");
  }

  function toggle(track: LibraryTrackSchema) {
    if (!track.id) return;
    setSelected((prev) => {
      const next = new Map(prev);
      if (next.has(track.id as string)) next.delete(track.id as string);
      else next.set(track.id as string, track);
      return next;
    });
  }

  function handleAdd() {
    if (selected.size === 0) return;
    addMutation.mutate({
      playlistId,
      data: { track_ids: Array.from(selected.keys()) },
    });
  }

  return (
    <ResponsiveDialog
      open={open}
      onOpenChange={(isOpen) => {
        setOpen(isOpen);
        if (!isOpen) reset();
      }}
      trigger={
        <Button variant="outline" size="sm">
          <Plus className="mr-1 size-4" />
          Add tracks
        </Button>
      }
    >
      <DialogHeader>
        <DialogTitle>Add tracks</DialogTitle>
      </DialogHeader>

      <Command
        className="rounded-lg border border-border-muted bg-surface"
        shouldFilter={false}
        loop
      >
        <div className="flex items-center gap-2 border-b border-border-muted px-3">
          <Search className="size-4 text-text-muted" />
          <Command.Input
            ref={inputRef}
            value={search}
            onValueChange={setSearch}
            placeholder="Search your library…"
            className="flex h-10 w-full bg-transparent text-sm text-text outline-none placeholder:text-text-faint"
          />
        </div>
        <Command.List className="max-h-72 overflow-y-auto p-1">
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
          {deferredSearch.length >= 2 && !isLoading && results.length === 0 && (
            <Command.Empty className="p-4 text-center text-sm text-text-muted">
              No tracks found.
            </Command.Empty>
          )}
          {results.map((track) => {
            const id = track.id ?? "";
            const isSelected = selected.has(id);
            const alreadyIn = existingTrackIds.has(id);
            return (
              <Command.Item
                key={id}
                value={id}
                onSelect={() => toggle(track)}
                className="flex cursor-pointer items-center gap-3 rounded-md px-3 py-2 text-sm hover:bg-surface-sunken aria-selected:bg-surface-sunken"
              >
                <span
                  className={`flex size-4 shrink-0 items-center justify-center rounded border ${
                    isSelected
                      ? "border-primary bg-primary text-primary-foreground"
                      : "border-border-muted"
                  }`}
                  aria-hidden="true"
                >
                  {isSelected && <Check className="size-3.5" strokeWidth={3} />}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="truncate font-medium text-text">
                    {track.title}
                  </div>
                  <div className="truncate text-xs text-text-muted">
                    {formatArtists(track.artists)}
                    {track.album && ` — ${track.album}`}
                  </div>
                </div>
                {alreadyIn && (
                  <span className="shrink-0 rounded bg-surface-sunken px-1.5 py-0.5 text-[11px] text-text-muted">
                    Added
                  </span>
                )}
                <div className="flex shrink-0 gap-1">
                  {track.connector_names.map((name) => (
                    <ConnectorIcon key={name} name={name} />
                  ))}
                </div>
              </Command.Item>
            );
          })}
        </Command.List>
      </Command>

      <DialogFooter>
        <Button
          onClick={handleAdd}
          disabled={selected.size === 0 || addMutation.isPending}
        >
          {addMutation.isPending
            ? "Adding…"
            : selected.size === 0
              ? "Add tracks"
              : `Add ${pluralize(selected.size, "track")}`}
        </Button>
      </DialogFooter>
    </ResponsiveDialog>
  );
}
