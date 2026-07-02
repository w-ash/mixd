import { useQueryClient } from "@tanstack/react-query";
import { Check, Plus } from "lucide-react";
import { useState } from "react";
import type { LibraryTrackSchema } from "#/api/generated/model";
import {
  getGetPlaylistApiV1PlaylistsPlaylistIdGetQueryKey,
  getGetPlaylistTracksApiV1PlaylistsPlaylistIdTracksGetQueryKey,
  getListPlaylistsApiV1PlaylistsGetQueryKey,
  useAddPlaylistTracksApiV1PlaylistsPlaylistIdTracksPost,
} from "#/api/generated/playlists/playlists";
import { CommandSearchList } from "#/components/shared/CommandSearchList";
import { Button } from "#/components/ui/button";
import {
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "#/components/ui/dialog";
import { ResponsiveDialog } from "#/components/ui/responsive-dialog";
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
  const queryClient = useQueryClient();

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

      <CommandSearchList
        limit={20}
        enabled={open}
        loop
        listClassName="max-h-72 overflow-y-auto p-1"
        placeholder="Search your library…"
        onSelect={toggle}
        rowLeading={(track) => {
          const isSelected = selected.has(track.id);
          return (
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
          );
        }}
        rowTrailing={(track) =>
          existingTrackIds.has(track.id) ? (
            <span className="shrink-0 rounded bg-surface-sunken px-1.5 py-0.5 text-[11px] text-text-muted">
              Added
            </span>
          ) : null
        }
      />

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
