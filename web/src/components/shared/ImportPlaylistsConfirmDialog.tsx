import { useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { invalidateTags } from "#/api/cache-tags";
import { useImportConnectorPlaylistsApiV1ConnectorsServicePlaylistsImportPost } from "#/api/generated/connectors/connectors";
import type {
  ConnectorMetadataSchema,
  OperationStartedResponse,
} from "#/api/generated/model";
import {
  isTerminalProgress,
  useOperationProgress,
} from "#/hooks/useOperationProgress";
import { useRunCompletedToast } from "#/hooks/useRunCompletedToast";
import { pluralize } from "#/lib/pluralize";
import type { SyncDirection } from "#/lib/sync-direction";
import { toasts } from "#/lib/toasts";
import { ConfirmationDialog } from "./ConfirmationDialog";
import type { PickedPlaylist } from "./ConnectorPlaylistPickerDialog";
import { DirectionChooser } from "./DirectionChooser";
import { ImportPlaylistResultRow } from "./ImportPlaylistResultRow";
import { OperationProgress } from "./OperationProgress";

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
  const [operationId, setOperationId] = useState<string | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const queryClient = useQueryClient();
  const label = connector.display_name;

  const importMut =
    useImportConnectorPlaylistsApiV1ConnectorsServicePlaylistsImportPost({
      mutation: {
        onSuccess: (response) => {
          if (response.status !== 202) return;
          const data = response.data as OperationStartedResponse;
          setOperationId(data.operation_id);
          setRunId(data.run_id ?? null);
        },
        meta: { errorLabel: `Failed to import ${label} playlists` },
      },
    });

  const { progress } = useOperationProgress(operationId);

  const isTerminal = isTerminalProgress(progress);

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

  // The generic run toast counts items; this one counts playlists and names the
  // ones that failed, so it replaces the default rather than adding to it.
  // `onTerminal` runs whoever won the ledger claim, so the picker refreshes
  // even when the global watcher announced the run first.
  useRunCompletedToast({
    operationId,
    runId,
    progress,
    operationType: "import_connector_playlists",
    buildToast: ({ progress: terminal, runId: auditRunId, navigate }) => {
      const { succeeded, skippedUnchanged, failed } = summary;
      const history = terminal.subOperationHistory;
      // "View log" deep-link when an audit run was persisted AND there is
      // something worth investigating (failure or partial outcome).
      const logAction =
        auditRunId !== null && (failed > 0 || succeeded > 0)
          ? {
              label: "View log",
              onClick: () => navigate(`/settings/imports?run=${auditRunId}`),
            }
          : undefined;
      if (failed > 0) {
        const firstFailures = Object.values(history)
          .filter((r) => r.outcome === "failed")
          .slice(0, 3)
          .map(
            (r) => `${r.playlistName ?? "Unknown"} — ${r.errorMessage ?? ""}`,
          )
          .join("\n");
        toasts.message("Import had errors", {
          description: firstFailures || undefined,
          action: logAction,
        });
      } else if (succeeded > 0) {
        const parts = [`Imported ${pluralize(succeeded, "playlist")}`];
        if (skippedUnchanged > 0)
          parts.push(`${skippedUnchanged} already up to date`);
        toasts.success(parts.join(" · "), { action: logAction });
      } else if (skippedUnchanged > 0) {
        toasts.info(
          `${pluralize(skippedUnchanged, "playlist")} already up to date`,
        );
      }
    },
    onTerminal: () => onImported?.(),
  });

  const count = playlists.length;
  const countLabel = pluralize(count, "playlist");
  const displayedNames = playlists.slice(0, 10).map((p) => p.name);
  const extraCount = count - displayedNames.length;

  const handleOpenChange = (nextOpen: boolean) => {
    onOpenChange(nextOpen);
    if (!nextOpen) {
      setOperationId(null);
      setRunId(null);
      // So the picker reflects the new link state on reopen.
      void invalidateTags(queryClient, ["connector-playlists"]);
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
            connector_playlist_identifiers: playlists.map((p) => p.id),
            sync_direction: direction,
          },
        });
      }}
    >
      {/* Phase 1 — Compose: direction toggle + playlist list */}
      {!operationId && (
        <div className="space-y-4">
          <DirectionChooser
            value={direction}
            onChange={setDirection}
            connectorLabel={label}
          />

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
