import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  getListConnectorPlaylistsApiV1ConnectorsServicePlaylistsGetQueryKey,
  useImportConnectorPlaylistsApiV1ConnectorsServicePlaylistsImportPost,
} from "#/api/generated/connectors/connectors";
import type {
  ConnectorMetadataSchema,
  OperationStartedResponse,
} from "#/api/generated/model";
import { getListPlaylistsApiV1PlaylistsGetQueryKey } from "#/api/generated/playlists/playlists";
import { Switch } from "#/components/ui/switch";
import { useOperationProgress } from "#/hooks/useOperationProgress";
import { pluralize } from "#/lib/pluralize";
import { toasts } from "#/lib/toasts";

import { ConfirmationDialog } from "./ConfirmationDialog";
import type { PickedPlaylist } from "./ConnectorPlaylistPickerDialog";
import { ImportPlaylistResultRow } from "./ImportPlaylistResultRow";
import { OperationProgress } from "./OperationProgress";

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
 * browser; this dialog picks the sync direction, kicks off an async
 * import, and streams per-playlist progress via SSE.
 *
 * Three inline phases in the same dialog shell:
 *
 * 1. **Compose**: direction toggle + playlist list + Import button.
 * 2. **Running**: ``<OperationProgress>`` (bar + ETA + phase message)
 *    plus a ticking per-playlist result list.
 * 3. **Done**: summary counts + Close button. The final toast variant
 *    matches the aggregate outcome (``error`` on any failure,
 *    ``info`` when every playlist was already up to date, ``success``
 *    when at least one was imported cleanly).
 */
export function ImportPlaylistsConfirmDialog({
  open,
  onOpenChange,
  connector,
  playlists,
  onImported,
}: ImportPlaylistsConfirmDialogProps) {
  const [direction, setDirection] = useState<SyncDirection>("pull");
  const [forceRefetch, setForceRefetch] = useState(false);
  const [operationId, setOperationId] = useState<string | null>(null);
  const queryClient = useQueryClient();
  const label = connector.display_name;

  const importMut =
    useImportConnectorPlaylistsApiV1ConnectorsServicePlaylistsImportPost({
      mutation: {
        onSuccess: (response) => {
          if (response.status !== 202) return;
          setOperationId(
            (response.data as OperationStartedResponse).operation_id,
          );
        },
        meta: { errorLabel: `Failed to import ${label} playlists` },
      },
    });

  const { progress } = useOperationProgress(operationId, {
    invalidateKeys: [
      getListConnectorPlaylistsApiV1ConnectorsServicePlaylistsGetQueryKey(
        connector.name,
      ),
      getListPlaylistsApiV1PlaylistsGetQueryKey(),
    ],
  });

  const isTerminal =
    progress !== null &&
    (progress.status === "completed" ||
      progress.status === "failed" ||
      progress.status === "cancelled");

  // Aggregate outcomes off the running sub_operation_history. We key by
  // connector_playlist_identifier to line up with the picker's selection.
  const summary = useMemo(() => {
    const history = progress?.subOperationHistory ?? {};
    const counts = { succeeded: 0, skippedUnchanged: 0, failed: 0 };
    for (const record of Object.values(history)) {
      if (record.outcome === "succeeded") counts.succeeded += 1;
      else if (record.outcome === "skipped_unchanged")
        counts.skippedUnchanged += 1;
      else if (record.outcome === "failed") counts.failed += 1;
    }
    return counts;
  }, [progress?.subOperationHistory]);

  // Fire the final toast exactly once per operation, when the SSE stream
  // reaches a terminal state. The ref guards against re-firing if React
  // re-renders while the terminal progress object is still the same.
  const toastedForOpIdRef = useRef<string | null>(null);
  useEffect(() => {
    if (!isTerminal || !operationId || progress === null) return;
    if (toastedForOpIdRef.current === operationId) return;
    toastedForOpIdRef.current = operationId;

    const { succeeded, skippedUnchanged, failed } = summary;
    const history = progress.subOperationHistory;
    if (failed > 0) {
      const firstFailures = Object.values(history)
        .filter((r) => r.outcome === "failed")
        .slice(0, 3)
        .map((r) => `${r.playlistName ?? "Unknown"} — ${r.errorMessage ?? ""}`)
        .join("\n");
      toasts.message("Import had errors", {
        description: firstFailures || undefined,
      });
    } else if (succeeded > 0) {
      const parts = [`Imported ${pluralize(succeeded, "playlist")}`];
      if (skippedUnchanged > 0)
        parts.push(`${skippedUnchanged} already up to date`);
      toasts.success(parts.join(" · "));
    } else if (skippedUnchanged > 0) {
      toasts.info(
        `${pluralize(skippedUnchanged, "playlist")} already up to date`,
      );
    }
    onImported?.();
  }, [isTerminal, operationId, progress, summary, onImported]);

  const count = playlists.length;
  const countLabel = pluralize(count, "playlist");
  const displayedNames = playlists.slice(0, 10).map((p) => p.name);
  const extraCount = count - displayedNames.length;

  const handleOpenChange = (nextOpen: boolean) => {
    onOpenChange(nextOpen);
    if (!nextOpen) {
      setOperationId(null);
      // Invalidate so the picker reflects the new link state on reopen.
      queryClient.invalidateQueries({
        queryKey:
          getListConnectorPlaylistsApiV1ConnectorsServicePlaylistsGetQueryKey(
            connector.name,
          ),
      });
    }
  };

  // Phase-aware button + state.
  const running = operationId !== null && !isTerminal;
  const confirmLabel = isTerminal
    ? "Close"
    : running
      ? "Running…"
      : `Import ${countLabel}`;
  const disabled = running || count === 0;

  return (
    <ConfirmationDialog
      open={open}
      onOpenChange={handleOpenChange}
      title={
        isTerminal
          ? "Import complete"
          : running
            ? `Importing ${countLabel}`
            : `Import ${countLabel}`
      }
      confirmLabel={confirmLabel}
      disabled={disabled}
      isPending={importMut.isPending || running}
      onConfirm={() => {
        if (isTerminal) {
          handleOpenChange(false);
          return;
        }
        if (count === 0) return;
        importMut.mutate({
          service: connector.name,
          data: {
            connector_playlist_ids: playlists.map((p) => p.id),
            sync_direction: direction,
            force: forceRefetch,
          },
        });
      }}
    >
      {/* Phase 1 — Compose: direction toggle + playlist list */}
      {!operationId && (
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
                <span className="block font-medium text-text">
                  Mixd-managed
                </span>
                <span className="block text-xs text-text-muted">
                  Mixd owns the truth and pushes changes to {label}. Good for
                  workflow output playlists.
                </span>
              </span>
            </label>
          </fieldset>

          <div className="flex items-start justify-between gap-3 rounded-md border p-3 hover:bg-accent/30">
            <label htmlFor="import-force-refetch" className="cursor-pointer">
              <span className="block font-medium text-text">
                Force re-fetch
              </span>
              <span className="block text-xs text-text-muted">
                Bypass the snapshot-fresh short-circuit and re-fetch from{" "}
                {label}. Use when you know the {label} playlist changed and the
                cached snapshot is stale.
              </span>
            </label>
            <Switch
              id="import-force-refetch"
              checked={forceRefetch}
              onCheckedChange={setForceRefetch}
              aria-label="Force re-fetch from connector"
              className="mt-1"
            />
          </div>

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
      )}

      {/* Phase 2 — Running / Phase 3 — Done */}
      {operationId && progress !== null && (
        <div className="space-y-4">
          <OperationProgress progress={progress} />

          {isTerminal && (
            <div className="flex flex-wrap gap-x-6 gap-y-2 text-sm text-text-muted">
              <span>
                <span className="font-medium text-text">
                  {summary.succeeded}
                </span>{" "}
                imported
              </span>
              <span>
                <span className="font-medium text-text">
                  {summary.skippedUnchanged}
                </span>{" "}
                already up to date
              </span>
              <span>
                <span className="font-medium text-text">{summary.failed}</span>{" "}
                failed
              </span>
            </div>
          )}

          <div className="max-h-64 overflow-y-auto rounded-md border border-text-faint/20 bg-surface-inset">
            {playlists.map((p) => {
              const record = progress.subOperationHistory[p.id] ?? null;
              return (
                <ImportPlaylistResultRow
                  key={p.id}
                  record={record}
                  fallbackName={p.name}
                  isActive={!isTerminal}
                  className="border-b border-text-faint/10 last:border-b-0"
                />
              );
            })}
          </div>
        </div>
      )}
    </ConfirmationDialog>
  );
}
