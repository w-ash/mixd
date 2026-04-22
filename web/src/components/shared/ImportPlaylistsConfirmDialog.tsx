import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import {
  getListConnectorPlaylistsApiV1ConnectorsServicePlaylistsGetQueryKey,
  useImportConnectorPlaylistsApiV1ConnectorsServicePlaylistsImportPost,
} from "#/api/generated/connectors/connectors";
import type { ConnectorMetadataSchema } from "#/api/generated/model";
import { getListPlaylistsApiV1PlaylistsGetQueryKey } from "#/api/generated/playlists/playlists";
import { pluralize } from "#/lib/pluralize";
import { toasts } from "#/lib/toasts";

import { ConfirmationDialog } from "./ConfirmationDialog";
import type { PickedPlaylist } from "./ConnectorPlaylistPickerDialog";

type SyncDirection = "pull" | "push";

interface ImportPlaylistsConfirmDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The connector the picker selected these playlists from. */
  connector: ConnectorMetadataSchema;
  /** Playlists selected in the picker. */
  playlists: PickedPlaylist[];
  /** Called after a successful import so the caller can close the picker. */
  onImported?: () => void;
}

/**
 * Step 2 of the import flow: the user has selected playlists in the
 * browser; this dialog picks the sync direction and fires the mutation.
 *
 * Uses the shared ConfirmationDialog shell — same pattern BulkTagDialog
 * follows. Aggregate resolved/unresolved track counts surface in the
 * post-import toast rather than a pre-import preview (which would require
 * full-track fetches per playlist, defeating the metadata-only browse).
 */
export function ImportPlaylistsConfirmDialog({
  open,
  onOpenChange,
  connector,
  playlists,
  onImported,
}: ImportPlaylistsConfirmDialogProps) {
  const [direction, setDirection] = useState<SyncDirection>("pull");
  const queryClient = useQueryClient();
  const label = connector.display_name;

  const importMut =
    useImportConnectorPlaylistsApiV1ConnectorsServicePlaylistsImportPost({
      mutation: {
        onSuccess: async (response) => {
          if (response.status !== 200) return;
          const { succeeded, skipped_unchanged, failed } = response.data;
          await queryClient.invalidateQueries({
            queryKey:
              getListConnectorPlaylistsApiV1ConnectorsServicePlaylistsGetQueryKey(
                connector.name,
              ),
          });
          await queryClient.invalidateQueries({
            queryKey: getListPlaylistsApiV1PlaylistsGetQueryKey(),
          });

          const sMsg =
            succeeded.length > 0
              ? `Imported ${pluralize(succeeded.length, "playlist")}`
              : "";
          const kMsg =
            skipped_unchanged.length > 0
              ? `${skipped_unchanged.length} unchanged`
              : "";
          const fMsg = failed.length > 0 ? `${failed.length} failed` : "";
          const parts = [sMsg, kMsg, fMsg].filter(Boolean);
          if (failed.length > 0 && succeeded.length === 0) {
            toasts.error(
              "Import failed",
              new Error(failed.map((f) => f.message).join("; ")),
            );
          } else if (parts.length > 0) {
            toasts.success(parts.join(" · "));
          }
          onImported?.();
          onOpenChange(false);
        },
        meta: { errorLabel: `Failed to import ${label} playlists` },
      },
    });

  const count = playlists.length;
  const countLabel = pluralize(count, "playlist");
  const displayedNames = playlists.slice(0, 10).map((p) => p.name);
  const extraCount = count - displayedNames.length;

  return (
    <ConfirmationDialog
      open={open}
      onOpenChange={onOpenChange}
      title={`Import ${countLabel}`}
      confirmLabel={`Import ${countLabel}`}
      disabled={count === 0}
      isPending={importMut.isPending}
      onConfirm={() => {
        if (count === 0) return;
        importMut.mutate({
          service: connector.name,
          data: {
            connector_playlist_ids: playlists.map((p) => p.id),
            sync_direction: direction,
          },
        });
      }}
    >
      <div className="space-y-4">
        <fieldset className="space-y-2">
          <legend className="font-display text-sm font-medium text-text">
            Sync direction
          </legend>
          <label className="flex cursor-pointer items-start gap-3 rounded-md border p-3 hover:bg-accent/30">
            <input
              type="radio"
              name="sync-direction"
              value="pull"
              checked={direction === "pull"}
              onChange={() => setDirection("pull")}
              className="mt-1"
            />
            <span>
              <span className="block font-medium text-text">
                {label}-managed
              </span>
              <span className="block text-xs text-text-muted">
                Mixd reads from {label}. Good for reference or bootstrap
                playlists you'll keep editing in {label}.
              </span>
            </span>
          </label>
          <label className="flex cursor-pointer items-start gap-3 rounded-md border p-3 hover:bg-accent/30">
            <input
              type="radio"
              name="sync-direction"
              value="push"
              checked={direction === "push"}
              onChange={() => setDirection("push")}
              className="mt-1"
            />
            <span>
              <span className="block font-medium text-text">Mixd-managed</span>
              <span className="block text-xs text-text-muted">
                Mixd owns the truth and pushes changes to {label}. Good for
                workflow output playlists.
              </span>
            </span>
          </label>
        </fieldset>

        {displayedNames.length > 0 && (
          <div>
            <p className="text-xs text-text-muted">Importing:</p>
            <ul className="mt-1 list-inside list-disc space-y-0.5 text-sm text-text">
              {displayedNames.map((name, i) => (
                // biome-ignore lint/suspicious/noArrayIndexKey: names may dup
                <li key={`${name}-${i}`} className="truncate">
                  {name}
                </li>
              ))}
              {extraCount > 0 && (
                <li className="text-text-muted">… and {extraCount} more</li>
              )}
            </ul>
          </div>
        )}
      </div>
    </ConfirmationDialog>
  );
}
