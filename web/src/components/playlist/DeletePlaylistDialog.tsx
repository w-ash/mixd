import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router";
import {
  getListPlaylistsApiV1PlaylistsGetQueryKey,
  useDeletePlaylistApiV1PlaylistsPlaylistIdDelete,
} from "#/api/generated/playlists/playlists";
import { ConfirmationDialog } from "#/components/shared/ConfirmationDialog";
import { Button } from "#/components/ui/button";

export function DeletePlaylistDialog({ playlistId }: { playlistId: string }) {
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const deleteMutation = useDeletePlaylistApiV1PlaylistsPlaylistIdDelete({
    mutation: {
      onSuccess: () => {
        queryClient.invalidateQueries({
          queryKey: getListPlaylistsApiV1PlaylistsGetQueryKey(),
        });
        navigate("/playlists");
      },
      meta: { errorLabel: "Failed to delete playlist" },
    },
  });

  return (
    <>
      <Button variant="destructive" size="sm" onClick={() => setOpen(true)}>
        Delete
      </Button>
      <ConfirmationDialog
        open={open}
        onOpenChange={setOpen}
        title="Delete Playlist"
        description="This action cannot be undone. The playlist and all its entries will be permanently removed."
        confirmLabel={
          deleteMutation.isPending ? "Deleting..." : "Delete permanently"
        }
        destructive
        isPending={deleteMutation.isPending}
        onConfirm={() => deleteMutation.mutate({ playlistId })}
      />
    </>
  );
}
