import { AlertTriangle, Info, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";

import { ApiError } from "#/api/client";
import type {
  OperationStartedResponse,
  SyncPreviewResponse,
} from "#/api/generated/model";
import {
  usePreviewPlaylistSyncApiV1PlaylistsPlaylistIdLinksLinkIdSyncPreviewGet,
  useSyncPlaylistLinkApiV1PlaylistsPlaylistIdLinksLinkIdSyncPost,
} from "#/api/generated/playlists/playlists";
import { ConfirmationDialog } from "#/components/shared/ConfirmationDialog";
import { ConnectorIcon } from "#/components/shared/ConnectorIcon";
import { DirectionChooser } from "#/components/shared/DirectionChooser";
import { getConnectorLabel } from "#/lib/connector-brand";
import { pluralize } from "#/lib/pluralize";
import type { SyncDirection } from "#/lib/sync-direction";
import { formatApiError, toasts } from "#/lib/toasts";

interface SyncConfirmationDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  playlistId: string;
  linkId: string;
  connectorName: string;
  playlistName: string;
  currentDirection: string;
  /** Called with the new operation_id once a sync is accepted (202). */
  onStarted: (operationId: string) => void;
}

/** Counts driving the destructive gate — from the preview, or refreshed by a
 *  stale-token 409 when the remote moved since the preview. */
interface DestructiveCounts {
  removals: number;
  total: number;
}

function PreviewContent({ preview }: { preview: SyncPreviewResponse }) {
  const label = getConnectorLabel(preview.connector_name);
  const target = preview.direction === "push" ? label : "your library";

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <ConnectorIcon name={preview.connector_name} />
        <span className="text-sm font-medium text-text">
          {preview.playlist_name}
        </span>
      </div>

      <div className="rounded-md bg-surface-inset p-3 text-sm text-text-muted space-y-1">
        {preview.tracks_to_add > 0 && (
          <p>
            <span className="text-status-success font-medium">
              +{preview.tracks_to_add}
            </span>{" "}
            tracks to add to {target}
          </p>
        )}
        {preview.tracks_to_remove > 0 && (
          <p>
            <span className="text-status-error font-medium">
              -{preview.tracks_to_remove}
            </span>{" "}
            tracks to remove from {target}
          </p>
        )}
        {preview.tracks_to_add === 0 && preview.tracks_to_remove === 0 && (
          <p>No changes — playlists are already in sync.</p>
        )}
        {preview.tracks_unchanged > 0 && (
          <p className="text-text-faint">
            {preview.tracks_unchanged} unchanged tracks hidden
          </p>
        )}
      </div>
    </div>
  );
}

function FirstSyncContent({
  connectorName,
  playlistName,
  direction,
}: {
  connectorName: string;
  playlistName: string;
  direction: string;
}) {
  const label = getConnectorLabel(connectorName);
  const description =
    direction === "push"
      ? `This will push your local tracks to "${playlistName}" on ${label}.`
      : `This will pull tracks from "${playlistName}" on ${label} into your library.`;

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <ConnectorIcon name={connectorName} />
        <span className="text-sm font-medium text-text">{playlistName}</span>
      </div>
      <div className="flex items-start gap-2 rounded-md bg-surface-inset p-3 text-sm text-text-muted">
        <Info className="mt-0.5 size-4 shrink-0 text-status-info" />
        <div>
          <p>This link has never been synced.</p>
          <p className="mt-1 text-text-faint">{description}</p>
        </div>
      </div>
    </div>
  );
}

/**
 * Confirmation dialog for a playlist sync. Fetches a preview of the diff, gates
 * a destructive sync behind a real `confirm_token` round-trip (the engine flags
 * destructiveness; a stale token re-prompts via 409 with fresh counts), and
 * owns the sync mutation so the dialog can stay open through a 409 or an error.
 */
export function SyncConfirmationDialog({
  open,
  onOpenChange,
  playlistId,
  linkId,
  connectorName,
  playlistName,
  currentDirection,
  onStarted,
}: SyncConfirmationDialogProps) {
  const [directionOverride, setDirectionOverride] = useState<string | null>(
    null,
  );
  // Token sent with the sync POST: seeded from the preview, refreshed by a 409.
  const [confirmToken, setConfirmToken] = useState<string | undefined>();
  // Fresh counts from a stale-token 409 (remote moved since the preview).
  const [staleCounts, setStaleCounts] = useState<DestructiveCounts | null>(
    null,
  );
  const [syncError, setSyncError] = useState<string | null>(null);

  const effectiveDirection = directionOverride ?? currentDirection;
  const label = getConnectorLabel(connectorName);

  const {
    data: previewData,
    isLoading: previewLoading,
    isError: previewError,
  } = usePreviewPlaylistSyncApiV1PlaylistsPlaylistIdLinksLinkIdSyncPreviewGet(
    playlistId,
    linkId,
    { direction_override: directionOverride ?? undefined },
    { query: { enabled: open, staleTime: 30_000 } },
  );

  const preview: SyncPreviewResponse | undefined =
    previewData?.status === 200 ? previewData.data : undefined;

  // Seed the confirm token from the preview; a new direction → new preview → new
  // token. A 409 overwrites this with the server's fresh token.
  useEffect(() => {
    if (preview?.confirm_token) setConfirmToken(preview.confirm_token);
  }, [preview?.confirm_token]);

  // Per-direction sync state: the confirm token plus any 409-derived destructive
  // counts/error. Cleared on a full reset and whenever the direction switches
  // (a new direction loads a fresh preview + token, so the old gate must drop).
  const clearSyncState = () => {
    setConfirmToken(undefined);
    setStaleCounts(null);
    setSyncError(null);
  };

  const reset = () => {
    clearSyncState();
    setDirectionOverride(null);
  };

  const handleDirectionChange = (direction: SyncDirection) => {
    clearSyncState();
    setDirectionOverride(direction);
  };

  const syncMut =
    useSyncPlaylistLinkApiV1PlaylistsPlaylistIdLinksLinkIdSyncPost({
      mutation: {
        onSuccess: (res) => {
          if (res.status !== 202) return;
          onStarted((res.data as OperationStartedResponse).operation_id);
          toasts.success(successMessage);
          onOpenChange(false);
          reset();
        },
        onError: (err) => {
          // Stale/absent token on a destructive sync: re-prompt with fresh
          // counts + token rather than failing. The dialog stays open.
          if (
            err instanceof ApiError &&
            err.status === 409 &&
            err.code === "CONFIRMATION_REQUIRED"
          ) {
            const d = err.details ?? {};
            if (d.confirm_token) setConfirmToken(d.confirm_token);
            setStaleCounts({
              removals: Number(d.removals ?? 0),
              total: Number(d.total ?? 0),
            });
            setSyncError(
              "The playlist changed since the preview — review the updated changes and confirm again.",
            );
            return;
          }
          // Any other error keeps the dialog up so the user sees it (BK-9).
          setSyncError(formatApiError(err).description ?? "Sync failed");
        },
      },
    });

  const hasComparisonData = preview?.has_comparison_data !== false;
  const isSafetyFlagged =
    preview?.safety_flagged === true || staleCounts !== null;
  const hasChanges =
    preview &&
    hasComparisonData &&
    (preview.tracks_to_add > 0 || preview.tracks_to_remove > 0);

  const removals =
    staleCounts?.removals ??
    preview?.safety_removals ??
    preview?.tracks_to_remove ??
    0;
  const total = staleCounts?.total ?? preview?.safety_total ?? 0;

  const successMessage = isSafetyFlagged
    ? `Removing ${pluralize(removals, "track")}…`
    : "Sync started";

  const confirmLabel = (() => {
    if (!preview) return "Sync";
    // Direction is owned by the DirectionChooser above; the button is the verb
    // (+ the count, the consequence). No "to/from {connector}" duplication.
    if (!hasComparisonData) return "Sync";
    if (isSafetyFlagged) return `Remove ${pluralize(removals, "track")}`;
    if (!hasChanges) return "Already in sync";
    const count = preview.tracks_to_add + preview.tracks_to_remove;
    return `Sync ${pluralize(count, "track")}`;
  })();

  return (
    <ConfirmationDialog
      open={open}
      onOpenChange={(isOpen) => {
        onOpenChange(isOpen);
        if (!isOpen) reset();
      }}
      title={isSafetyFlagged ? "Remove tracks" : "Sync Preview"}
      description={`Review what will change before syncing ${playlistName}.`}
      confirmLabel={confirmLabel}
      destructive={isSafetyFlagged}
      isPending={syncMut.isPending}
      disabled={
        previewLoading || (hasComparisonData && !hasChanges && !previewError)
      }
      onConfirm={() => {
        setSyncError(null);
        syncMut.mutate({
          playlistId,
          linkId,
          data: {
            direction_override: directionOverride ?? undefined,
            confirm_token: confirmToken,
          },
        });
      }}
    >
      <DirectionChooser
        value={effectiveDirection as SyncDirection}
        onChange={handleDirectionChange}
        connectorLabel={label}
      />

      {previewLoading && (
        <div className="flex items-center justify-center gap-2 py-6 text-text-muted">
          <Loader2 className="size-4 animate-spin" />
          <span className="text-sm">Loading preview...</span>
        </div>
      )}

      {previewError && (
        <div className="flex items-center gap-2 rounded-md bg-status-error/10 p-3 text-sm text-status-error">
          <AlertTriangle className="size-4 shrink-0" />
          <span>Failed to load sync preview. You can still sync manually.</span>
        </div>
      )}

      {preview &&
        (hasComparisonData ? (
          <>
            <PreviewContent preview={preview} />
            {isSafetyFlagged && (
              <div className="flex items-start gap-2 rounded-md border-l-2 border-status-error bg-status-error/10 p-4 text-sm text-status-error">
                <AlertTriangle className="mt-0.5 size-4 shrink-0" />
                <div>
                  <p className="font-medium text-text">
                    This sync will remove {pluralize(removals, "track")}
                    {total > 0 ? ` of ${total}` : ""} from "{playlistName}".
                  </p>
                  <p className="mt-1 text-status-error/80">
                    {preview.safety_message ??
                      "Removing these tracks can't be undone."}
                  </p>
                </div>
              </div>
            )}
          </>
        ) : (
          <FirstSyncContent
            connectorName={connectorName}
            playlistName={playlistName}
            direction={effectiveDirection}
          />
        ))}

      {syncError && (
        <div className="flex items-start gap-2 rounded-md bg-status-warning/10 p-3 text-sm text-status-warning">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" />
          <span>{syncError}</span>
        </div>
      )}
    </ConfirmationDialog>
  );
}
