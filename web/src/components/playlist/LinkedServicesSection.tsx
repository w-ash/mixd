import { useQueryClient } from "@tanstack/react-query";
import { ArrowLeftRight, Loader2, RefreshCw, Unlink } from "lucide-react";
import { useEffect, useState } from "react";
import type { PlaylistLinkSchema } from "#/api/generated/model";
import {
  getGetPlaylistApiV1PlaylistsPlaylistIdGetQueryKey,
  getGetPlaylistTracksApiV1PlaylistsPlaylistIdTracksGetQueryKey,
  getListPlaylistLinksApiV1PlaylistsPlaylistIdLinksGetQueryKey,
  getListPlaylistsApiV1PlaylistsGetQueryKey,
  useDeletePlaylistLinkApiV1PlaylistsPlaylistIdLinksLinkIdDelete,
  useListPlaylistLinksApiV1PlaylistsPlaylistIdLinksGet,
  useUpdatePlaylistLinkApiV1PlaylistsPlaylistIdLinksLinkIdPatch,
} from "#/api/generated/playlists/playlists";
import { ConnectorListItem } from "#/components/shared/ConnectorListItem";
import { OperationProgress } from "#/components/shared/OperationProgress";
import { SectionHeader } from "#/components/shared/SectionHeader";
import {
  StatusIndicator,
  syncStatusVariant,
} from "#/components/shared/StatusIndicator";
import { SyncConfirmationDialog } from "#/components/shared/SyncConfirmationDialog";
import { UnmatchedBadge } from "#/components/shared/UnmatchedBadge";
import { Button } from "#/components/ui/button";
import { Skeleton } from "#/components/ui/skeleton";
import { useOperationProgress } from "#/hooks/useOperationProgress";
import { getConnectorLabel } from "#/lib/connector-brand";
import { formatRelativeTime } from "#/lib/format";
import { formatSyncResults, getSyncStatusConfig } from "#/lib/sync-status";
import { toasts } from "#/lib/toasts";
import { LinkPlaylistDialog } from "./LinkPlaylistDialog";
import { invalidateLinkQueries } from "./link-queries";

export function LinkedServicesSection({ playlistId }: { playlistId: string }) {
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
                ? `Mixd → ${label}`
                : `${label} → Mixd`;
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
