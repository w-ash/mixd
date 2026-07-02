import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import {
  getGetPlaylistApiV1PlaylistsPlaylistIdGetQueryKey,
  getListPlaylistsApiV1PlaylistsGetQueryKey,
  useUpdatePlaylistApiV1PlaylistsPlaylistIdPatch,
} from "#/api/generated/playlists/playlists";
import { Button } from "#/components/ui/button";
import {
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "#/components/ui/dialog";
import { Input } from "#/components/ui/input";
import { ResponsiveDialog } from "#/components/ui/responsive-dialog";
import { toasts } from "#/lib/toasts";

export function EditPlaylistDialog({
  playlistId,
  currentName,
  currentDescription,
}: {
  playlistId: string;
  currentName: string;
  currentDescription: string | null | undefined;
}) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(currentName);
  const [description, setDescription] = useState(currentDescription ?? "");
  const queryClient = useQueryClient();

  const detailQueryKey =
    getGetPlaylistApiV1PlaylistsPlaylistIdGetQueryKey(playlistId);

  const updateMutation = useUpdatePlaylistApiV1PlaylistsPlaylistIdPatch({
    mutation: {
      onMutate: async ({ data }) => {
        // Cancel in-flight refetches so they don't overwrite our optimistic update
        await queryClient.cancelQueries({ queryKey: detailQueryKey });
        const previous = queryClient.getQueryData(detailQueryKey);
        // Optimistically update the detail cache
        queryClient.setQueryData(detailQueryKey, (old: unknown) => {
          if (!old || typeof old !== "object") return old;
          return { ...old, ...(data as Record<string, unknown>) };
        });
        return { previous };
      },
      onSuccess: () => {
        setOpen(false);
      },
      // Custom onError keeps the optimistic-rollback logic; toast is
      // handled locally rather than via the global MutationCache handler
      // to keep rollback and notification in one atomic step.
      onError: (error: Error, _vars, context) => {
        if (context?.previous) {
          queryClient.setQueryData(detailQueryKey, context.previous);
        }
        toasts.error("Failed to update playlist", error);
      },
      meta: { suppressErrorToast: true },
      onSettled: () => {
        // Always refetch authoritative data after mutation settles
        queryClient.invalidateQueries({ queryKey: detailQueryKey });
        queryClient.invalidateQueries({
          queryKey: getListPlaylistsApiV1PlaylistsGetQueryKey(),
        });
      },
    },
  });

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    updateMutation.mutate({
      playlistId,
      data: {
        name: name.trim(),
        description: description.trim() || undefined,
      },
    });
  }

  return (
    <ResponsiveDialog
      open={open}
      onOpenChange={(isOpen) => {
        setOpen(isOpen);
        if (isOpen) {
          setName(currentName);
          setDescription(currentDescription ?? "");
        }
      }}
      trigger={
        <Button variant="outline" size="sm">
          Edit
        </Button>
      }
    >
      <form onSubmit={handleSubmit}>
        <DialogHeader>
          <DialogTitle>Edit Playlist</DialogTitle>
          <DialogDescription>
            Update the playlist name or description.
          </DialogDescription>
        </DialogHeader>

        <div className="mt-4 space-y-4">
          <div className="space-y-2">
            <label
              htmlFor="edit-name"
              className="text-sm font-medium text-text"
            >
              Name
            </label>
            <Input
              id="edit-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              autoFocus
            />
          </div>
          <div className="space-y-2">
            <label
              htmlFor="edit-description"
              className="text-sm font-medium text-text"
            >
              Description
            </label>
            <Input
              id="edit-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Optional description"
            />
          </div>
        </div>

        <DialogFooter className="mt-6">
          <Button
            type="button"
            variant="outline"
            onClick={() => setOpen(false)}
          >
            Cancel
          </Button>
          <Button
            type="submit"
            disabled={!name.trim() || updateMutation.isPending}
          >
            {updateMutation.isPending ? "Saving..." : "Save"}
          </Button>
        </DialogFooter>
      </form>
    </ResponsiveDialog>
  );
}
