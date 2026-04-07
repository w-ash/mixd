import { AlertTriangle, Info, Loader2 } from "lucide-react";
import { useState } from "react";

import type { SyncPreviewResponse } from "#/api/generated/model";
import { usePreviewPlaylistSyncApiV1PlaylistsPlaylistIdLinksLinkIdSyncPreviewGet } from "#/api/generated/playlists/playlists";
import { ConfirmationDialog } from "#/components/shared/ConfirmationDialog";
import {
  ConnectorIcon,
  getConnectorLabel,
} from "#/components/shared/ConnectorIcon";
import { Button } from "#/components/ui/button";

interface SyncConfirmationDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  playlistId: string;
  linkId: string;
  connectorName: string;
  playlistName: string;
  currentDirection: string;
  isPending: boolean;
  onConfirm: (directionOverride?: string) => void;
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
            <span className="text-green-500 font-medium">
              +{preview.tracks_to_add}
            </span>{" "}
            tracks to add to {target}
          </p>
        )}
        {preview.tracks_to_remove > 0 && (
          <p>
            <span className="text-red-500 font-medium">
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
            {preview.tracks_unchanged} tracks unchanged
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
        <Info className="mt-0.5 size-4 shrink-0 text-blue-400" />
        <div>
          <p>This link has never been synced.</p>
          <p className="mt-1 text-text-faint">{description}</p>
        </div>
      </div>
    </div>
  );
}

/**
 * Confirmation dialog for playlist sync operations.
 *
 * Fetches a preview of what the sync would change, displays it,
 * and lets the user confirm or cancel. For never-synced links,
 * shows a first-sync message instead of diff counts.
 */
export function SyncConfirmationDialog({
  open,
  onOpenChange,
  playlistId,
  linkId,
  connectorName,
  playlistName,
  currentDirection,
  isPending,
  onConfirm,
}: SyncConfirmationDialogProps) {
  const [directionOverride, setDirectionOverride] = useState<string | null>(
    null,
  );
  const effectiveDirection = directionOverride ?? currentDirection;

  const {
    data: previewData,
    isLoading: previewLoading,
    isError: previewError,
  } = usePreviewPlaylistSyncApiV1PlaylistsPlaylistIdLinksLinkIdSyncPreviewGet(
    playlistId,
    linkId,
    { direction_override: directionOverride ?? undefined },
    {
      query: {
        enabled: open,
        staleTime: 30_000,
      },
    },
  );

  const preview: SyncPreviewResponse | undefined =
    previewData?.status === 200 ? previewData.data : undefined;

  const label = getConnectorLabel(connectorName);
  const hasComparisonData = preview?.has_comparison_data !== false;
  const isSafetyFlagged = preview?.safety_flagged === true;
  const hasChanges =
    preview &&
    hasComparisonData &&
    (preview.tracks_to_add > 0 || preview.tracks_to_remove > 0);

  const confirmLabel = (() => {
    if (!preview) return "Sync";
    if (!hasComparisonData) {
      return effectiveDirection === "push"
        ? `Sync to ${label}`
        : `Sync from ${label}`;
    }
    if (!hasChanges) return "Already in sync";
    const count = preview.tracks_to_add + preview.tracks_to_remove;
    return effectiveDirection === "push"
      ? `Sync ${count} tracks to ${label}`
      : `Sync ${count} tracks from ${label}`;
  })();

  return (
    <ConfirmationDialog
      open={open}
      onOpenChange={(isOpen) => {
        onOpenChange(isOpen);
        if (!isOpen) setDirectionOverride(null);
      }}
      title="Sync Preview"
      description={`Review what will change before syncing ${playlistName}.`}
      confirmLabel={confirmLabel}
      destructive={isSafetyFlagged}
      isPending={isPending}
      disabled={
        previewLoading || (hasComparisonData && !hasChanges && !previewError)
      }
      onConfirm={() => onConfirm(directionOverride ?? undefined)}
    >
      {/* Direction toggle */}
      <div className="flex items-center gap-2 text-sm">
        <span className="text-text-muted">Direction:</span>
        <div className="flex gap-1">
          <Button
            variant={effectiveDirection === "push" ? "default" : "outline"}
            size="sm"
            className="h-7 text-xs"
            onClick={() =>
              setDirectionOverride(currentDirection === "push" ? null : "push")
            }
          >
            Local &rarr; {label}
          </Button>
          <Button
            variant={effectiveDirection === "pull" ? "default" : "outline"}
            size="sm"
            className="h-7 text-xs"
            onClick={() =>
              setDirectionOverride(currentDirection === "pull" ? null : "pull")
            }
          >
            {label} &rarr; Local
          </Button>
        </div>
      </div>

      {/* Preview content */}
      {previewLoading && (
        <div className="flex items-center justify-center gap-2 py-6 text-text-muted">
          <Loader2 className="size-4 animate-spin" />
          <span className="text-sm">Loading preview...</span>
        </div>
      )}

      {previewError && (
        <div className="flex items-center gap-2 rounded-md bg-red-500/10 p-3 text-sm text-red-400">
          <AlertTriangle className="size-4 shrink-0" />
          <span>Failed to load sync preview. You can still sync manually.</span>
        </div>
      )}

      {preview &&
        (hasComparisonData ? (
          <>
            <PreviewContent preview={preview} />
            {isSafetyFlagged && (
              <div className="flex items-start gap-2 rounded-md border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-400">
                <AlertTriangle className="mt-0.5 size-4 shrink-0" />
                <div>
                  <p className="font-medium">Destructive sync detected</p>
                  <p className="mt-1 text-red-400/80">
                    {preview.safety_message}
                  </p>
                  <p className="mt-1 text-red-400/80">
                    Direction:{" "}
                    {effectiveDirection === "push"
                      ? `Local \u2192 ${label}`
                      : `${label} \u2192 Local`}
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
    </ConfirmationDialog>
  );
}
