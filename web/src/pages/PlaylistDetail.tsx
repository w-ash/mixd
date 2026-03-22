import { useQueryClient } from "@tanstack/react-query";
import {
  ArrowDownLeft,
  ArrowRight,
  ArrowUpRight,
  HelpCircle,
  Link2,
  Loader2,
  Music,
  Unlink,
} from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { toast } from "sonner";
import type {
  PlaylistLinkSchema,
  SyncStartedResponse,
} from "@/api/generated/model";
import {
  getGetPlaylistApiV1PlaylistsPlaylistIdGetQueryKey,
  getGetPlaylistTracksApiV1PlaylistsPlaylistIdTracksGetQueryKey,
  getListPlaylistLinksApiV1PlaylistsPlaylistIdLinksGetQueryKey,
  getListPlaylistsApiV1PlaylistsGetQueryKey,
  useCreatePlaylistLinkApiV1PlaylistsPlaylistIdLinksPost,
  useDeletePlaylistApiV1PlaylistsPlaylistIdDelete,
  useDeletePlaylistLinkApiV1PlaylistsPlaylistIdLinksLinkIdDelete,
  useGetPlaylistApiV1PlaylistsPlaylistIdGet,
  useGetPlaylistTracksApiV1PlaylistsPlaylistIdTracksGet,
  useListPlaylistLinksApiV1PlaylistsPlaylistIdLinksGet,
  useSyncPlaylistLinkApiV1PlaylistsPlaylistIdLinksLinkIdSyncPost,
  useUpdatePlaylistApiV1PlaylistsPlaylistIdPatch,
  useUpdatePlaylistLinkApiV1PlaylistsPlaylistIdLinksLinkIdPatch,
} from "@/api/generated/playlists/playlists";
import { STALE } from "@/api/query-client";
import { PageHeader } from "@/components/layout/PageHeader";
import { BackLink } from "@/components/shared/BackLink";
import { ConfirmationDialog } from "@/components/shared/ConfirmationDialog";
import {
  ConnectorIcon,
  getConnectorLabel,
} from "@/components/shared/ConnectorIcon";
import { ConnectorListItem } from "@/components/shared/ConnectorListItem";
import { EmptyState } from "@/components/shared/EmptyState";
import { OperationProgress } from "@/components/shared/OperationProgress";
import { SectionHeader } from "@/components/shared/SectionHeader";
import {
  StatusIndicator,
  syncStatusVariant,
} from "@/components/shared/StatusIndicator";
import { SyncConfirmationDialog } from "@/components/shared/SyncConfirmationDialog";
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useOperationProgress } from "@/hooks/useOperationProgress";
import {
  decodeHtmlEntities,
  formatArtists,
  formatDate,
  formatDuration,
  formatRelativeTime,
  formatTotalDuration,
} from "@/lib/format";
import { formatSyncResults, getSyncStatusConfig } from "@/lib/sync-status";

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

// ─── Delete Playlist Dialog ──────────────────────────────────────────────────

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

// ─── Edit Playlist Dialog ────────────────────────────────────────────────────

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
      onError: (error: Error, _vars, context) => {
        // Rollback to previous data on failure
        if (context?.previous) {
          queryClient.setQueryData(detailQueryKey, context.previous);
        }
        toast.error("Failed to update playlist", {
          description: error.message,
        });
      },
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

// ─── Shared Invalidation ─────────────────────────────────────────────────────

function invalidateLinkQueries(
  queryClient: ReturnType<typeof useQueryClient>,
  playlistId: number,
) {
  queryClient.invalidateQueries({
    queryKey:
      getListPlaylistLinksApiV1PlaylistsPlaylistIdLinksGetQueryKey(playlistId),
  });
  queryClient.invalidateQueries({
    queryKey: getGetPlaylistApiV1PlaylistsPlaylistIdGetQueryKey(playlistId),
  });
  queryClient.invalidateQueries({
    queryKey: getListPlaylistsApiV1PlaylistsGetQueryKey(),
  });
}

// ─── Link Playlist Dialog ────────────────────────────────────────────────────

function LinkPlaylistDialog({ playlistId }: { playlistId: number }) {
  const [open, setOpen] = useState(false);
  const [connector, setConnector] = useState("spotify");
  const [playlistInput, setPlaylistInput] = useState("");
  const [direction, setDirection] = useState("push");
  const queryClient = useQueryClient();

  const createLink = useCreatePlaylistLinkApiV1PlaylistsPlaylistIdLinksPost({
    mutation: {
      onSuccess: () => {
        invalidateLinkQueries(queryClient, playlistId);
        setOpen(false);
        setPlaylistInput("");
        toast.success("Playlist linked");
      },
      onError: (error: Error) => {
        toast.error("Failed to link playlist", {
          description: error.message,
        });
      },
    },
  });

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!playlistInput.trim()) return;
    createLink.mutate({
      playlistId,
      data: {
        connector,
        connector_playlist_id: playlistInput.trim(),
        sync_direction: direction,
      },
    });
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(isOpen) => {
        setOpen(isOpen);
        if (isOpen) {
          setPlaylistInput("");
          setConnector("spotify");
          setDirection("push");
        }
      }}
    >
      <DialogTrigger asChild>
        <Button variant="outline" size="sm">
          <Link2 className="mr-1.5 size-3.5" />
          Link Playlist
        </Button>
      </DialogTrigger>
      <DialogContent>
        <form onSubmit={handleSubmit}>
          <DialogHeader>
            <DialogTitle>Link External Playlist</DialogTitle>
            <DialogDescription>
              Connect this playlist to an external service for syncing.
            </DialogDescription>
          </DialogHeader>

          <div className="mt-4 space-y-4">
            <div className="space-y-2">
              <label
                htmlFor="link-connector"
                className="text-sm font-medium text-text"
              >
                Service
              </label>
              <Select value={connector} onValueChange={setConnector}>
                <SelectTrigger id="link-connector">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="spotify">Spotify</SelectItem>
                  <SelectItem value="apple_music">Apple Music</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <label
                htmlFor="link-playlist-id"
                className="text-sm font-medium text-text"
              >
                Playlist ID or URL
              </label>
              <Input
                id="link-playlist-id"
                value={playlistInput}
                onChange={(e) => setPlaylistInput(e.target.value)}
                placeholder="Paste Spotify URL or playlist ID"
                required
                autoFocus
              />
              <p className="text-xs text-text-muted font-body">
                Accepts a playlist URL, URI, or raw ID. The playlist will be
                validated immediately.
              </p>
            </div>
            <div className="space-y-2">
              <label
                htmlFor="link-direction"
                className="text-sm font-medium text-text"
              >
                Sync Direction
              </label>
              <Select value={direction} onValueChange={setDirection}>
                <SelectTrigger id="link-direction">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="push">
                    Push — Mixd controls, syncs to{" "}
                    {getConnectorLabel(connector)}
                  </SelectItem>
                  <SelectItem value="pull">
                    Pull — {getConnectorLabel(connector)} controls, syncs to
                    Mixd
                  </SelectItem>
                </SelectContent>
              </Select>
              <p className="text-xs text-text-faint font-body">
                {direction === "push"
                  ? "Your local playlist is the source of truth. Changes here push to the external service."
                  : "The external playlist is the source of truth. Changes there pull into your library."}
              </p>
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
              disabled={!playlistInput.trim() || createLink.isPending}
            >
              {createLink.isPending ? (
                <>
                  <Loader2 className="mr-1.5 size-3.5 animate-spin" />
                  Validating...
                </>
              ) : (
                "Link"
              )}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

// ─── Linked Services Section ─────────────────────────────────────────────────

function LinkedServicesSection({ playlistId }: { playlistId: number }) {
  const queryClient = useQueryClient();
  const [syncOperationId, setSyncOperationId] = useState<string | null>(null);
  const [syncDialogLink, setSyncDialogLink] =
    useState<PlaylistLinkSchema | null>(null);

  const { progress: syncProgress, isActive: isSyncing } = useOperationProgress(
    syncOperationId,
    {
      invalidateKeys: [
        getGetPlaylistTracksApiV1PlaylistsPlaylistIdTracksGetQueryKey(
          playlistId,
        ),
        getListPlaylistLinksApiV1PlaylistsPlaylistIdLinksGetQueryKey(
          playlistId,
        ),
        getGetPlaylistApiV1PlaylistsPlaylistIdGetQueryKey(playlistId),
        getListPlaylistsApiV1PlaylistsGetQueryKey(),
      ],
    },
  );

  // Clear stale operation ID once the operation finishes
  useEffect(() => {
    if (
      syncProgress?.status === "completed" ||
      syncProgress?.status === "failed"
    ) {
      setSyncOperationId(null);
    }
  }, [syncProgress?.status]);

  const { data: linksData, isLoading } =
    useListPlaylistLinksApiV1PlaylistsPlaylistIdLinksGet(playlistId);

  const links: PlaylistLinkSchema[] =
    linksData?.status === 200 ? linksData.data : [];

  const deleteLinkMutation =
    useDeletePlaylistLinkApiV1PlaylistsPlaylistIdLinksLinkIdDelete({
      mutation: {
        onSuccess: () => {
          invalidateLinkQueries(queryClient, playlistId);
          toast.success("Playlist unlinked");
        },
        onError: (error: Error) => {
          toast.error("Failed to unlink", { description: error.message });
        },
      },
    });

  const syncMutation =
    useSyncPlaylistLinkApiV1PlaylistsPlaylistIdLinksLinkIdSyncPost({
      mutation: {
        onSuccess: (res) => {
          if (res.status === 202) {
            setSyncOperationId((res.data as SyncStartedResponse).operation_id);
            toast.success("Sync started");
          }
          invalidateLinkQueries(queryClient, playlistId);
        },
        onError: (error: Error) => {
          toast.error("Sync failed", { description: error.message });
        },
      },
    });

  const updateLinkMutation =
    useUpdatePlaylistLinkApiV1PlaylistsPlaylistIdLinksLinkIdPatch({
      mutation: {
        onSuccess: () => {
          invalidateLinkQueries(queryClient, playlistId);
        },
        onError: (error: Error) => {
          toast.error("Failed to update direction", {
            description: error.message,
          });
        },
      },
    });

  if (isLoading) {
    return (
      <div className="space-y-2">
        <Skeleton className="h-5 w-32" />
        <Skeleton className="h-12 w-full" />
      </div>
    );
  }

  return (
    <section className="mb-8">
      <div className="mb-3 flex items-center justify-between">
        <SectionHeader title="Linked Services" />
        <LinkPlaylistDialog playlistId={playlistId} />
      </div>

      {syncProgress && (
        <OperationProgress progress={syncProgress} className="mb-3" />
      )}

      {links.length === 0 ? (
        <div className="rounded-md border-l-2 border-border bg-surface-inset px-4 py-3">
          <p className="font-body text-sm text-text-muted">
            No services linked. Link an external playlist to enable syncing.
          </p>
        </div>
      ) : (
        <div className="space-y-2">
          {links.map((link) => {
            const label = getConnectorLabel(link.connector_name);
            const directionLabel =
              link.sync_direction === "push"
                ? `Local \u2192 ${label}`
                : `${label} \u2192 Local`;
            const syncButtonLabel =
              link.sync_direction === "push"
                ? `Sync to ${label}`
                : `Sync from ${label}`;
            const syncResults = formatSyncResults(
              link.last_sync_tracks_added,
              link.last_sync_tracks_removed,
            );

            return (
              <ConnectorListItem
                key={link.id}
                connectorName={link.connector_name}
                actions={
                  <>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-7 px-2 text-xs text-text-muted hover:text-text"
                      disabled={
                        syncMutation.isPending ||
                        isSyncing ||
                        link.sync_status === "syncing"
                      }
                      onClick={() => setSyncDialogLink(link)}
                    >
                      {link.sync_status === "syncing" ||
                      syncMutation.isPending ||
                      isSyncing ? (
                        <Loader2 className="mr-1 size-3 animate-spin" />
                      ) : link.sync_direction === "push" ? (
                        <ArrowUpRight className="mr-1 size-3" />
                      ) : (
                        <ArrowDownLeft className="mr-1 size-3" />
                      )}
                      {syncButtonLabel}
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-7 px-2 text-xs text-text-muted hover:text-destructive"
                      title="Removes the sync link. Your external playlist is unchanged."
                      disabled={deleteLinkMutation.isPending}
                      onClick={() =>
                        deleteLinkMutation.mutate({
                          playlistId,
                          linkId: link.id,
                        })
                      }
                    >
                      <Unlink className="mr-1 size-3" />
                      Remove link
                    </Button>
                  </>
                }
              >
                {/* Playlist name + direction + status */}
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                  <span className="truncate font-body text-sm text-text-muted">
                    {link.connector_playlist_name ?? link.connector_playlist_id}
                  </span>
                  <button
                    type="button"
                    className="flex items-center gap-1 rounded px-1.5 py-0.5 text-xs text-text-muted transition-colors hover:bg-surface-elevated hover:text-text"
                    title={`Click to switch to ${link.sync_direction === "push" ? "pull" : "push"}`}
                    disabled={updateLinkMutation.isPending}
                    onClick={() =>
                      updateLinkMutation.mutate({
                        playlistId,
                        linkId: link.id,
                        data: {
                          sync_direction:
                            link.sync_direction === "push" ? "pull" : "push",
                        },
                      })
                    }
                  >
                    <ArrowRight className="size-3.5" />
                    <span className="text-[11px]">{directionLabel}</span>
                  </button>
                  <StatusIndicator
                    variant={syncStatusVariant(link.sync_status)}
                    label={getSyncStatusConfig(link.sync_status).label}
                    detail={
                      link.sync_status === "error"
                        ? (link.last_sync_error ?? undefined)
                        : (syncResults ??
                          formatRelativeTime(link.last_synced) ??
                          undefined)
                    }
                  />
                </div>
              </ConnectorListItem>
            );
          })}
        </div>
      )}

      {syncDialogLink && (
        <SyncConfirmationDialog
          open
          onOpenChange={(open) => !open && setSyncDialogLink(null)}
          playlistId={playlistId}
          linkId={syncDialogLink.id}
          connectorName={syncDialogLink.connector_name}
          playlistName={
            syncDialogLink.connector_playlist_name ??
            syncDialogLink.connector_playlist_id
          }
          currentDirection={syncDialogLink.sync_direction}
          isPending={syncMutation.isPending}
          onConfirm={(directionOverride) => {
            syncMutation.mutate({
              playlistId,
              linkId: syncDialogLink.id,
              data: { direction_override: directionOverride, confirmed: true },
            });
            setSyncDialogLink(null);
          }}
        />
      )}
    </section>
  );
}

// ─── Main Page ───────────────────────────────────────────────────────────────

export function PlaylistDetail() {
  const { id } = useParams<{ id: string }>();
  const playlistId = Number(id);

  const {
    data: playlistData,
    isLoading: playlistLoading,
    isError: playlistError,
  } = useGetPlaylistApiV1PlaylistsPlaylistIdGet(playlistId, {
    query: { staleTime: STALE.MEDIUM },
  });

  const { data: tracksData, isLoading: tracksLoading } =
    useGetPlaylistTracksApiV1PlaylistsPlaylistIdTracksGet(
      playlistId,
      undefined,
      {
        query: { staleTime: STALE.MEDIUM },
      },
    );

  if (playlistLoading) return <DetailSkeleton />;

  if (playlistError) {
    return (
      <EmptyState
        icon={<HelpCircle className="size-10" />}
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
      <title>{playlist.name} — Mixd</title>
      <BackLink to="/playlists">Playlists</BackLink>
      <PageHeader
        title={playlist.name}
        description={
          playlist.description
            ? decodeHtmlEntities(playlist.description)
            : undefined
        }
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
              {playlist.connector_links.map((link) => (
                <ConnectorIcon
                  key={link.connector_name}
                  name={link.connector_name}
                />
              ))}
            </span>
          </>
        )}
      </div>

      {/* Linked Services */}
      <LinkedServicesSection playlistId={playlistId} />

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
          icon={<Music className="size-10" />}
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
                <TableCell>
                  <Link
                    to={`/library/${entry.track.id}`}
                    className="font-medium text-text hover:text-primary transition-colors"
                  >
                    {entry.track.title}
                  </Link>
                </TableCell>
                <TableCell className="text-text-muted">
                  {formatArtists(entry.track.artists)}
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
