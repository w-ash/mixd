import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate, useParams } from "react-router";
import { toast } from "sonner";
import {
  getGetPlaylistApiV1PlaylistsPlaylistIdGetQueryKey,
  getListPlaylistsApiV1PlaylistsGetQueryKey,
  useDeletePlaylistApiV1PlaylistsPlaylistIdDelete,
  useGetPlaylistApiV1PlaylistsPlaylistIdGet,
  useGetPlaylistTracksApiV1PlaylistsPlaylistIdTracksGet,
  useUpdatePlaylistApiV1PlaylistsPlaylistIdPatch,
} from "@/api/generated/playlists/playlists";
import { PageHeader } from "@/components/layout/PageHeader";
import { ConnectorIcon } from "@/components/shared/ConnectorIcon";
import { EmptyState } from "@/components/shared/EmptyState";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatDate } from "@/lib/format";

function formatDuration(ms: number | null | undefined): string {
  if (!ms) return "\u2014";
  const minutes = Math.floor(ms / 60_000);
  const seconds = Math.floor((ms % 60_000) / 1000);
  return `${minutes}:${seconds.toString().padStart(2, "0")}`;
}

function formatTotalDuration(ms: number): string {
  if (ms <= 0) return "0 min";
  const totalMinutes = Math.round(ms / 60_000);
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  if (hours === 0) return `${minutes} min`;
  if (minutes === 0) return `${hours} hr`;
  return `${hours} hr ${minutes} min`;
}

function DetailSkeleton() {
  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-4 w-96" />
      </div>
      <div className="space-y-3">
        {Array.from({ length: 8 }).map((_, i) => (
          // biome-ignore lint/suspicious/noArrayIndexKey: static skeleton placeholders
          <div key={i} className="flex items-center gap-4">
            <Skeleton className="h-5 w-8" />
            <Skeleton className="h-5 w-56" />
            <Skeleton className="h-5 w-36" />
            <Skeleton className="h-5 w-40" />
            <Skeleton className="h-5 w-12" />
          </div>
        ))}
      </div>
    </div>
  );
}

function DeletePlaylistDialog({ playlistId }: { playlistId: number }) {
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
      onError: (error: Error) => {
        toast.error("Failed to delete playlist", {
          description: error.message,
        });
      },
    },
  });

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="destructive" size="sm">
          Delete
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Delete Playlist</DialogTitle>
          <DialogDescription>
            This action cannot be undone. The playlist and all its entries will
            be permanently removed.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            disabled={deleteMutation.isPending}
            onClick={() => deleteMutation.mutate({ playlistId })}
          >
            {deleteMutation.isPending ? "Deleting..." : "Delete"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function EditPlaylistDialog({
  playlistId,
  currentName,
  currentDescription,
}: {
  playlistId: number;
  currentName: string;
  currentDescription: string | null | undefined;
}) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(currentName);
  const [description, setDescription] = useState(currentDescription ?? "");
  const queryClient = useQueryClient();

  const updateMutation = useUpdatePlaylistApiV1PlaylistsPlaylistIdPatch({
    mutation: {
      onSuccess: () => {
        queryClient.invalidateQueries({
          queryKey:
            getGetPlaylistApiV1PlaylistsPlaylistIdGetQueryKey(playlistId),
        });
        queryClient.invalidateQueries({
          queryKey: getListPlaylistsApiV1PlaylistsGetQueryKey(),
        });
        setOpen(false);
      },
      onError: (error: Error) => {
        toast.error("Failed to update playlist", {
          description: error.message,
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
    <Dialog
      open={open}
      onOpenChange={(isOpen) => {
        setOpen(isOpen);
        if (isOpen) {
          setName(currentName);
          setDescription(currentDescription ?? "");
        }
      }}
    >
      <DialogTrigger asChild>
        <Button variant="outline" size="sm">
          Edit
        </Button>
      </DialogTrigger>
      <DialogContent>
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
      </DialogContent>
    </Dialog>
  );
}

export function PlaylistDetail() {
  const { id } = useParams<{ id: string }>();
  const playlistId = Number(id);

  const {
    data: playlistData,
    isLoading: playlistLoading,
    isError: playlistError,
  } = useGetPlaylistApiV1PlaylistsPlaylistIdGet(playlistId);

  const { data: tracksData, isLoading: tracksLoading } =
    useGetPlaylistTracksApiV1PlaylistsPlaylistIdTracksGet(playlistId);

  if (playlistLoading) return <DetailSkeleton />;

  if (playlistError) {
    return (
      <EmptyState
        icon="?"
        heading="Playlist not found"
        description="This playlist doesn't exist or has been deleted."
      />
    );
  }

  const playlist = playlistData?.status === 200 ? playlistData.data : undefined;
  if (!playlist) return null;

  const tracksResponse =
    tracksData?.status === 200 ? tracksData.data : undefined;
  const entries = tracksResponse?.data ?? [];

  return (
    <div>
      <PageHeader
        title={playlist.name}
        description={playlist.description ?? undefined}
        action={
          <div className="flex gap-2">
            <EditPlaylistDialog
              playlistId={playlist.id}
              currentName={playlist.name}
              currentDescription={playlist.description}
            />
            <DeletePlaylistDialog playlistId={playlist.id} />
          </div>
        }
      />

      <div className="mb-6 flex items-center gap-3 text-sm text-text-muted">
        <span>
          {playlist.track_count}{" "}
          {playlist.track_count === 1 ? "track" : "tracks"}
        </span>
        {entries.length > 0 && (
          <>
            <span aria-hidden="true">&middot;</span>
            <span>
              {formatTotalDuration(
                entries.reduce((sum, e) => sum + (e.track.duration_ms ?? 0), 0),
              )}
            </span>
          </>
        )}
        {playlist.updated_at && (
          <>
            <span aria-hidden="true">&middot;</span>
            <span>Updated {formatDate(playlist.updated_at)}</span>
          </>
        )}
        {playlist.connector_links.length > 0 && (
          <>
            <span aria-hidden="true">&middot;</span>
            <span className="flex items-center gap-2">
              {playlist.connector_links.map((connector) => (
                <ConnectorIcon key={connector} name={connector} />
              ))}
            </span>
          </>
        )}
      </div>

      {tracksLoading && (
        <div className="space-y-3">
          {Array.from({ length: 5 }).map((_, i) => (
            // biome-ignore lint/suspicious/noArrayIndexKey: static skeleton placeholders
            <div key={i} className="flex items-center gap-4">
              <Skeleton className="h-5 w-8" />
              <Skeleton className="h-5 w-56" />
              <Skeleton className="h-5 w-36" />
              <Skeleton className="h-5 w-40" />
              <Skeleton className="h-5 w-12" />
            </div>
          ))}
        </div>
      )}

      {!tracksLoading && entries.length === 0 && (
        <EmptyState
          icon="♫"
          heading="This playlist is empty"
          description="Add tracks by linking a connector playlist or using workflows."
        />
      )}

      {!tracksLoading && entries.length > 0 && (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-12 text-right">#</TableHead>
              <TableHead>Title</TableHead>
              <TableHead>Artists</TableHead>
              <TableHead>Album</TableHead>
              <TableHead className="w-20 text-right">Duration</TableHead>
              <TableHead className="w-32 text-right">Added</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {entries.map((entry) => (
              <TableRow key={entry.position}>
                <TableCell className="text-right tabular-nums text-text-muted">
                  {entry.position}
                </TableCell>
                <TableCell className="font-medium">
                  {entry.track.title}
                </TableCell>
                <TableCell className="text-text-muted">
                  {entry.track.artists.map((a) => a.name).join(", ")}
                </TableCell>
                <TableCell className="text-text-muted">
                  {entry.track.album ?? "\u2014"}
                </TableCell>
                <TableCell className="text-right tabular-nums text-text-muted">
                  {formatDuration(entry.track.duration_ms)}
                </TableCell>
                <TableCell className="text-right text-sm text-text-muted">
                  {formatDate(entry.added_at)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}
