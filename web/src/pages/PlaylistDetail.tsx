import { useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowLeftRight,
  HelpCircle,
  Link2,
  ListMusic,
  Loader2,
  Music,
  RefreshCw,
  Unlink,
} from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router";
import { useGetConnectorsApiV1ConnectorsGet } from "#/api/generated/connectors/connectors";
import type { PlaylistLinkSchema } from "#/api/generated/model";
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
  useRepairPlaylistUnresolvedApiV1PlaylistsPlaylistIdRepairPost,
  useUpdatePlaylistApiV1PlaylistsPlaylistIdPatch,
  useUpdatePlaylistLinkApiV1PlaylistsPlaylistIdLinksLinkIdPatch,
} from "#/api/generated/playlists/playlists";
import { STALE } from "#/api/query-client";
import { PageHeader } from "#/components/layout/PageHeader";
import { AddTracksDialog } from "#/components/playlist/AddTracksDialog";
import { PlaylistTrackEditor } from "#/components/playlist/PlaylistTrackEditor";
import { BackLink } from "#/components/shared/BackLink";
import { ConfirmationDialog } from "#/components/shared/ConfirmationDialog";
import { ConnectorIcon } from "#/components/shared/ConnectorIcon";
import { ConnectorListItem } from "#/components/shared/ConnectorListItem";
import { ConnectorPlaylistPickerDialog } from "#/components/shared/ConnectorPlaylistPickerDialog";
import { DirectionChooser } from "#/components/shared/DirectionChooser";
import { EmptyState } from "#/components/shared/EmptyState";
import { OperationProgress } from "#/components/shared/OperationProgress";
import { SectionHeader } from "#/components/shared/SectionHeader";
import {
  StatusIndicator,
  syncStatusVariant,
} from "#/components/shared/StatusIndicator";
import { SyncConfirmationDialog } from "#/components/shared/SyncConfirmationDialog";
import {
  DetailHeaderSkeleton,
  ListRowsSkeleton,
} from "#/components/shared/skeletons";
import { UnmatchedBadge } from "#/components/shared/UnmatchedBadge";
import { Button } from "#/components/ui/button";
import {
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "#/components/ui/dialog";
import { Input } from "#/components/ui/input";
import { ResponsiveDialog } from "#/components/ui/responsive-dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "#/components/ui/select";
import { Skeleton } from "#/components/ui/skeleton";
import { useOperationProgress } from "#/hooks/useOperationProgress";
import { getConnectorLabel } from "#/lib/connector-brand";
import {
  decodeHtmlEntities,
  formatDate,
  formatRelativeTime,
  formatTotalDuration,
} from "#/lib/format";
import { pluralize } from "#/lib/pluralize";
import type { SyncDirection } from "#/lib/sync-direction";
import { formatSyncResults, getSyncStatusConfig } from "#/lib/sync-status";
import { toasts } from "#/lib/toasts";

function DetailSkeleton() {
  return (
    <div className="space-y-6">
      <DetailHeaderSkeleton />
      <ListRowsSkeleton
        rows={8}
        bars={["h-5 w-8", "h-5 w-56", "h-5 w-36", "h-5 w-40", "h-5 w-12"]}
      />
    </div>
  );
}

// ─── Delete Playlist Dialog ──────────────────────────────────────────────────

function DeletePlaylistDialog({ playlistId }: { playlistId: string }) {
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

// ─── Edit Playlist Dialog ────────────────────────────────────────────────────

function EditPlaylistDialog({
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

// ─── Shared Invalidation ─────────────────────────────────────────────────────

function invalidateLinkQueries(
  queryClient: ReturnType<typeof useQueryClient>,
  playlistId: string,
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

function LinkPlaylistDialog({ playlistId }: { playlistId: string }) {
  const [open, setOpen] = useState(false);
  const [playlistInput, setPlaylistInput] = useState("");
  // Friendly name of a playlist chosen via Browse — labels the browse button so
  // the user sees "Selected: …" instead of only the raw identifier in the input.
  const [pickedName, setPickedName] = useState<string | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [direction, setDirection] = useState<SyncDirection>("push");
  const queryClient = useQueryClient();

  const { data: connectorsData } = useGetConnectorsApiV1ConnectorsGet({
    query: { staleTime: STALE.STATIC },
  });

  // Only connectors that advertise ``playlist_sync`` can accept a link —
  // MusicBrainz and Apple Music (coming soon) are filtered out automatically.
  const linkableConnectors =
    connectorsData?.status === 200
      ? connectorsData.data.filter((c) =>
          c.capabilities.includes("playlist_sync"),
        )
      : [];

  const defaultConnector = linkableConnectors[0]?.name ?? "";
  const [connector, setConnector] = useState(defaultConnector);

  // Ensure the state reflects the first real connector once the query lands.
  useEffect(() => {
    if (!connector && defaultConnector) setConnector(defaultConnector);
  }, [connector, defaultConnector]);

  const selectedConnector = linkableConnectors.find(
    (c) => c.name === connector,
  );
  // Browse-to-pick reuses the import browse list, which needs the
  // ``playlist_import`` capability (GET /connectors/{service}/playlists). A
  // sync-only connector without it falls back to paste-an-ID.
  const canBrowse =
    selectedConnector?.capabilities.includes("playlist_import") ?? false;
  const placeholder = selectedConnector
    ? `Paste ${selectedConnector.display_name} URL or playlist ID`
    : "Paste playlist URL or ID";

  const createLink = useCreatePlaylistLinkApiV1PlaylistsPlaylistIdLinksPost({
    mutation: {
      onSuccess: () => {
        invalidateLinkQueries(queryClient, playlistId);
        setOpen(false);
        setPlaylistInput("");
        toasts.success("Playlist linked");
      },
      meta: { errorLabel: "Failed to link playlist" },
    },
  });

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!playlistInput.trim()) return;
    createLink.mutate({
      playlistId,
      data: {
        connector,
        connector_playlist_identifier: playlistInput.trim(),
        sync_direction: direction,
      },
    });
  }

  return (
    <>
      <ResponsiveDialog
        open={open}
        onOpenChange={(isOpen) => {
          setOpen(isOpen);
          if (isOpen) {
            setPlaylistInput("");
            setPickedName(null);
            setPickerOpen(false);
            setConnector(defaultConnector);
            setDirection("push");
          }
        }}
        trigger={
          <Button variant="outline" size="sm">
            <Link2 className="mr-1.5 size-3.5" />
            Link Playlist
          </Button>
        }
      >
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
              <Select
                value={connector}
                onValueChange={(next) => {
                  setConnector(next);
                  // A picked/typed playlist belongs to the previous service —
                  // drop it so a switch can't submit one service's identifier
                  // against another.
                  setPlaylistInput("");
                  setPickedName(null);
                }}
              >
                <SelectTrigger id="link-connector">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {linkableConnectors.map((c) => (
                    <SelectItem key={c.name} value={c.name}>
                      {c.display_name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <span className="text-sm font-medium text-text">Playlist</span>
              {canBrowse && (
                <Button
                  type="button"
                  variant="outline"
                  className="w-full justify-start"
                  onClick={() => setPickerOpen(true)}
                >
                  <ListMusic className="mr-1.5 size-3.5" />
                  {pickedName
                    ? `Selected: ${pickedName}`
                    : `Browse ${selectedConnector?.display_name ?? ""} playlists`}
                </Button>
              )}
              <Input
                id="link-playlist-id"
                value={playlistInput}
                onChange={(e) => {
                  setPlaylistInput(e.target.value);
                  // Typing overrides a browsed pick — drop the stale name label.
                  if (pickedName) setPickedName(null);
                }}
                placeholder={placeholder}
                required
                autoFocus={!canBrowse}
              />
              <p className="text-xs text-text-muted font-body">
                {canBrowse
                  ? "Or paste a playlist URL, URI, or raw ID."
                  : "Accepts a playlist URL, URI, or raw ID."}{" "}
                The playlist will be validated immediately.
              </p>
            </div>
            <DirectionChooser
              value={direction}
              onChange={setDirection}
              connectorLabel={getConnectorLabel(connector)}
              legend="Sync Direction"
            />
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
      </ResponsiveDialog>
      {selectedConnector && (
        <ConnectorPlaylistPickerDialog
          open={pickerOpen}
          onOpenChange={setPickerOpen}
          connector={selectedConnector}
          mode="select"
          onConfirm={(picked) => {
            const first = picked[0];
            if (first) {
              setPlaylistInput(first.id);
              setPickedName(first.name);
            }
            setPickerOpen(false);
          }}
        />
      )}
    </>
  );
}

// ─── Linked Services Section ─────────────────────────────────────────────────

function LinkedServicesSection({ playlistId }: { playlistId: string }) {
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
          toasts.success("Playlist unlinked");
        },
        meta: { errorLabel: "Failed to unlink" },
      },
    });

  const updateLinkMutation =
    useUpdatePlaylistLinkApiV1PlaylistsPlaylistIdLinksLinkIdPatch({
      mutation: {
        onSuccess: () => {
          invalidateLinkQueries(queryClient, playlistId);
        },
        meta: { errorLabel: "Failed to update direction" },
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
                ? `Mixd \u2192 ${label}`
                : `${label} \u2192 Mixd`;
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
                      disabled={isSyncing || link.sync_status === "syncing"}
                      onClick={() => setSyncDialogLink(link)}
                    >
                      {link.sync_status === "syncing" || isSyncing ? (
                        <Loader2 className="mr-1 size-3 animate-spin" />
                      ) : (
                        <RefreshCw className="mr-1 size-3" />
                      )}
                      Sync
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
                    {link.connector_playlist_name ??
                      link.connector_playlist_identifier}
                  </span>
                  <button
                    type="button"
                    className="flex items-center gap-1 rounded px-1.5 py-0.5 text-xs text-text-muted transition-colors hover:bg-surface-elevated hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50"
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
                    <ArrowLeftRight className="size-3 shrink-0" />
                    <span className="whitespace-nowrap text-[11px]">
                      {directionLabel}
                    </span>
                  </button>
                  {link.sync_status === "never_synced" ? (
                    // "Never synced" is an absence, not a status — muted text,
                    // no icon. Icon+color+text is reserved for synced/syncing/error.
                    <span className="font-body text-xs text-text-muted">
                      Never synced
                    </span>
                  ) : (
                    <StatusIndicator
                      variant={syncStatusVariant(link.sync_status)}
                      label={getSyncStatusConfig(link.sync_status).label}
                      detail={
                        link.sync_status === "error"
                          ? (link.last_sync_error ?? undefined)
                          : (syncResults ??
                            (link.last_synced
                              ? formatRelativeTime(link.last_synced)
                              : undefined))
                      }
                    />
                  )}
                  <UnmatchedBadge count={link.last_sync_tracks_unmatched} />
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
            syncDialogLink.connector_playlist_identifier
          }
          currentDirection={syncDialogLink.sync_direction}
          onStarted={(operationId) => {
            setSyncOperationId(operationId);
            invalidateLinkQueries(queryClient, playlistId);
            setSyncDialogLink(null);
          }}
        />
      )}
    </section>
  );
}

// ─── Main Page ───────────────────────────────────────────────────────────────

/** Status mark for an entry that couldn't be matched to a known track. */
/** Roll-up + bulk repair for a playlist's unresolved entries. Silent at zero. */
function RepairUnresolvedBar({
  playlistId,
  count,
}: {
  playlistId: string;
  count: number;
}) {
  const queryClient = useQueryClient();
  const repairMut =
    useRepairPlaylistUnresolvedApiV1PlaylistsPlaylistIdRepairPost({
      mutation: {
        onSuccess: (res) => {
          if (res.status !== 200) return;
          const { repaired, still_unresolved } = res.data;
          if (repaired === 0) {
            toasts.info("No new matches found yet");
          } else if (still_unresolved === 0) {
            toasts.success(`Repaired ${pluralize(repaired, "track")}`);
          } else {
            toasts.success(
              `Repaired ${repaired} · ${still_unresolved} still unresolved`,
            );
          }
          queryClient.invalidateQueries({
            queryKey:
              getGetPlaylistTracksApiV1PlaylistsPlaylistIdTracksGetQueryKey(
                playlistId,
              ),
          });
        },
        meta: { errorLabel: "Repair failed" },
      },
    });

  if (count === 0) return null;

  return (
    <div className="mb-6 flex flex-wrap items-center justify-between gap-3 rounded-md border-l-2 border-status-expired bg-surface-inset px-4 py-3">
      <div className="flex items-center gap-2 text-sm text-text-muted">
        <AlertTriangle
          className="size-4 shrink-0 text-status-expired"
          aria-hidden="true"
        />
        <span>{pluralize(count, "track")} couldn't be matched.</span>
      </div>
      <Button
        variant="outline"
        size="sm"
        disabled={repairMut.isPending}
        onClick={() => repairMut.mutate({ playlistId })}
      >
        {repairMut.isPending && (
          <Loader2 className="mr-1 size-3 animate-spin" />
        )}
        Repair unresolved ({count})
      </Button>
    </div>
  );
}

export function PlaylistDetail() {
  const { id } = useParams<{ id: string }>();
  const playlistId = id ?? "";

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
  const unresolvedCount = entries.filter((e) => e.is_resolved === false).length;
  // Track ids already in the playlist — drives the "Added" badge in the
  // Add-Tracks modal (re-adding is still allowed; duplicates are intentional).
  const existingTrackIds = new Set(
    entries
      .map((e) => e.track.id)
      .filter((id): id is string => typeof id === "string"),
  );

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
            <AddTracksDialog
              playlistId={playlist.id}
              existingTrackIds={existingTrackIds}
            />
            <EditPlaylistDialog
              playlistId={playlist.id}
              currentName={playlist.name}
              currentDescription={playlist.description}
            />
            <DeletePlaylistDialog playlistId={playlist.id} />
          </div>
        }
      />

      <div className="mb-6 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-text-muted">
        <span>{pluralize(playlist.track_count, "track")}</span>
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

      {/* Unresolved tracks — first-class, with bulk repair */}
      <RepairUnresolvedBar playlistId={playlistId} count={unresolvedCount} />

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
          description="Add tracks with the Add Tracks button, link a connector playlist, or build it with a workflow."
        />
      )}

      {!tracksLoading && entries.length > 0 && (
        <PlaylistTrackEditor playlistId={playlistId} entries={entries} />
      )}
    </div>
  );
}
