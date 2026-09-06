import { AlertTriangle } from "lucide-react";
import type React from "react";
import { useEffect, useState } from "react";
import {
  useGetConnectorPlayPollingApiV1ConnectorsServicePlayPollingGet,
  useSetConnectorPlayPollingApiV1ConnectorsServicePlayPollingPut,
} from "#/api/generated/connectors/connectors";
import {
  useCancelSpotifyHistoryQueueApiV1ImportsSpotifyHistoryQueueDelete,
  useExportLastfmLikesApiV1ImportsLastfmLikesPost,
  useGetCheckpointsApiV1ImportsCheckpointsGet,
  useGetSpotifyHistoryQueueApiV1ImportsSpotifyHistoryQueueGet,
  useImportAppleRecentApiV1ImportsAppleRecentPost,
  useImportLastfmHistoryApiV1ImportsLastfmHistoryPost,
  useImportSpotifyHistoryApiV1ImportsSpotifyHistoryPost,
  useImportSpotifyLikesApiV1ImportsSpotifyLikesPost,
  useImportSpotifyRecentApiV1ImportsSpotifyRecentPost,
} from "#/api/generated/imports/imports";
import type {
  CheckpointStatusSchema,
  ImportLastfmHistoryRequestMode,
  SyncTargetSchema,
  SyncTargetSchemaId,
} from "#/api/generated/model";
import { STALE } from "#/api/query-client";
import { PageHeader } from "#/components/layout/PageHeader";
import { ConnectorIcon } from "#/components/shared/ConnectorIcon";
import {
  DatabaseUnavailable,
  isDatabaseUnavailable,
} from "#/components/shared/DatabaseUnavailable";
import { FileUpload } from "#/components/shared/FileUpload";
import { OperationProgress } from "#/components/shared/OperationProgress";
import { ScheduleCard } from "#/components/shared/ScheduleCard";
import { SectionHeader } from "#/components/shared/SectionHeader";
import { Button } from "#/components/ui/button";
import { Switch } from "#/components/ui/switch";
import { useImportOperation } from "#/hooks/useImportOperation";
import type { OperationProgress as OperationProgressState } from "#/hooks/useOperationProgress";
import { useOperationProgress } from "#/hooks/useOperationProgress";
import { useRunCompletedToast } from "#/hooks/useRunCompletedToast";
import { useSyncScheduleController } from "#/hooks/useScheduleController";
import { useSyncTargetBlock } from "#/hooks/useSyncTargetBlock";
import { formatCount, formatDateTime } from "#/lib/format";
import { isSettled } from "#/lib/import-queue";
import { claimRunToast } from "#/lib/operation-toast-ledger";
import { pluralSuffix } from "#/lib/pluralize";
import { describeMinutes } from "#/lib/schedule";
import { type RunOperationType, toasts } from "#/lib/toasts";
import { cn } from "#/lib/utils";
import { ImportQueueManifest } from "#/pages/settings/ImportQueueManifest";

// ─── Sync targets ───────────────────────────────────────────────

/**
 * Automatic-sync control for a sync target. Only the background-syncable cards
 * have one; the file-upload imports (which can't run unattended) render none.
 *
 * Which control appears is the server's call, not a prop: a `self_managed`
 * target gets the read-only cadence plus toggle instead of the daily/weekly
 * picker. That is not cosmetic — there is one schedule row per (user, target),
 * and a self-managed target's interval is rewritten by the poller after every
 * poll, so saving it through the picker would overwrite the adaptive cadence
 * and switch the backoff off. The backend enforces the same rule by keeping it
 * out of `USER_SCHEDULABLE_TARGETS`, which makes its upsert route 400.
 */
function SyncScheduleField({ target }: { target: SyncTargetSchema }) {
  if (target.self_managed) {
    // The play-polling endpoints are keyed by connector-registry service.
    return <PlayPollingField service={target.service} />;
  }
  return <UserScheduleField targetId={target.id} />;
}

function UserScheduleField({ targetId }: { targetId: SyncTargetSchemaId }) {
  const controller = useSyncScheduleController(targetId);
  return (
    <div className="mt-3 border-t border-border-muted pt-3">
      <p className="mb-2 font-display text-xs text-text-muted">
        Automatic sync
      </p>
      <ScheduleCard {...controller} />
    </div>
  );
}

// ─── Operation Card ──────────────────────────────────────────────

interface OperationCardProps {
  connector: string;
  title: string;
  description: string;
  checkpoint: CheckpointStatusSchema | undefined;
  operationId: string | null;
  runId: string | null;
  operationType: RunOperationType;
  isPending: boolean;
  onTrigger: () => void;
  triggerLabel?: string;
  /** Card-local reason the trigger can't fire (nothing selected, work already
   * in flight). Connector readiness comes from the sync target instead. */
  triggerDisabled?: boolean;
  /** Background-sync target id (e.g. `lastfm:plays`). When set, the card gates
   * its trigger on the target's readiness and shows an automatic-sync control;
   * which one is the server's call — see `SyncScheduleField`. */
  syncTarget?: SyncTargetSchemaId;
  /** Extra card body rendered above the progress bar. */
  children?: React.ReactNode;
  /** Card body needing the live progress the card is already subscribed to —
   * a second `useOperationProgress` on the same id opens a second stream. */
  renderDetail?: (progress: OperationProgressState | null) => React.ReactNode;
  /** Suppress the card's own bar when the detail renders one for the same work. */
  hideProgressBar?: boolean;
}

function OperationCard({
  connector,
  title,
  description,
  checkpoint,
  operationId,
  runId,
  operationType,
  isPending,
  onTrigger,
  triggerLabel = "Import",
  triggerDisabled,
  syncTarget,
  children,
  renderDetail,
  hideProgressBar = false,
}: OperationCardProps) {
  const { progress, isActive } = useOperationProgress(operationId);
  const { target, block: blocked } = useSyncTargetBlock(
    syncTarget,
    triggerLabel.toLowerCase(),
  );

  useRunCompletedToast({ operationId, runId, progress, operationType });

  return (
    <div className="rounded-xl border border-border bg-surface-elevated shadow-elevated p-5">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <ConnectorIcon name={connector} />
            <h3 className="font-display text-base font-semibold">{title}</h3>
          </div>
          <p className="mt-0.5 text-sm text-text-muted">{description}</p>
        </div>
        <Button
          size="sm"
          disabled={
            isPending || isActive || triggerDisabled || blocked !== null
          }
          onClick={onTrigger}
          className="self-start lg:self-auto"
        >
          {isPending ? "Starting..." : isActive ? "Running..." : triggerLabel}
        </Button>
      </div>

      {blocked && (
        <p className={cn("mt-2 text-xs", blocked.className)}>{blocked.text}</p>
      )}

      {children && <div className="mt-3">{children}</div>}

      {renderDetail?.(progress)}

      {progress && !hideProgressBar && (
        <OperationProgress progress={progress} className="mt-3" />
      )}

      {target && <SyncScheduleField target={target} />}

      <PollStatusLine checkpoint={checkpoint} />

      <p className="mt-3 text-right text-xs text-text-faint">
        Last sync:{" "}
        <span className="font-mono text-text-muted">
          {formatDateTime(checkpoint?.last_sync_timestamp)}
        </span>
      </p>
    </div>
  );
}

/**
 * "Last checked" plus polling health, for channels that are polled.
 *
 * Distinct from "Last sync" above it: that is when the user last *listened*,
 * this is when mixd last *checked*. On an idle account the former ages forever
 * while the latter stays current, and only the second answers "is this working?"
 *
 * Renders nothing for checkpoints that aren't polled — most aren't, and showing
 * them an empty status would read as a fault.
 */
function PollStatusLine({
  checkpoint,
}: {
  checkpoint: CheckpointStatusSchema | undefined;
}) {
  if (!checkpoint?.last_polled_at && !checkpoint?.poll_health) return null;

  return (
    <div className="mt-3 space-y-1 text-right text-xs text-text-faint">
      <p>
        Last checked:{" "}
        <span className="font-mono text-text-muted">
          {formatDateTime(checkpoint.last_polled_at)}
        </span>
        {checkpoint.poll_health === "overdue" && (
          <span className="ml-2 text-status-error">Overdue</span>
        )}
        {checkpoint.poll_health === "healthy" && (
          <span className="ml-2 text-status-success">Up to date</span>
        )}
      </p>
      {checkpoint.possible_gap && (
        // A saturated window means plays may already be gone from Spotify's
        // side, where nothing can recover them. The actionable remedy is a
        // second observer, so the copy says that rather than colouring a dot.
        <p className="flex items-center justify-end gap-1 text-status-warning">
          <AlertTriangle className="h-3 w-3" aria-hidden />
          <span>
            Listening outpaced the 50-play window — connect Last.fm so nothing
            is missed.
          </span>
        </p>
      )}
    </div>
  );
}

/**
 * Read-only cadence plus an on/off switch for a self-managed poll schedule.
 *
 * Deliberately not the shared `SchedulePicker` — see `SyncScheduleField`, which
 * decides from the server's `self_managed` flag which of the two a target gets.
 */
function PlayPollingField({ service }: { service: string }) {
  const { data, isLoading } =
    useGetConnectorPlayPollingApiV1ConnectorsServicePlayPollingGet(service, {
      query: { staleTime: STALE.SLOW, retry: false },
    });
  const state = data?.status === 200 ? data.data : null;

  const toggle = useSetConnectorPlayPollingApiV1ConnectorsServicePlayPollingPut(
    {
      mutation: {
        meta: { errorLabel: "Failed to update automatic sync" },
      },
    },
  );

  if (isLoading) return null;

  const enabled = state?.enabled ?? false;
  return (
    <div className="mt-3 border-t border-border-muted pt-3">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="font-display text-xs text-text-muted">Automatic sync</p>
          <p className="mt-0.5 text-xs text-text-faint">
            {enabled && state?.interval_minutes
              ? `Every ${describeMinutes(state.interval_minutes)}`
              : "Off"}
          </p>
        </div>
        {/* Enabled whenever the connector is usable — turning polling ON is the
            whole point, and the switch must be able to create the schedule the
            first time rather than waiting for a re-auth to do it. */}
        <Switch
          checked={enabled}
          disabled={toggle.isPending}
          onCheckedChange={(next) =>
            toggle.mutate({ service, data: { enabled: next } })
          }
          aria-label="Automatic play polling"
        />
      </div>
      {enabled && (
        <p className="mt-1 text-xs text-text-faint">
          Checks more often while you're listening, less often when you're not.
        </p>
      )}
    </div>
  );
}

// ─── Import Operations ──────────────────────────────────────────

const HISTORY_MODES = [
  {
    value: "recent",
    label: "Recent",
    desc: "Last 90 days of scrobbles",
  },
  {
    value: "incremental",
    label: "Since last import",
    desc: "Everything new since your last import",
  },
  {
    value: "full",
    label: "Full",
    desc: "Complete listening history (may be slow)",
  },
] as const;

/** Find a checkpoint for a service+entity combo from the pre-fetched list. */
function findCheckpoint(
  checkpoints: CheckpointStatusSchema[],
  service: string,
  entityType: string,
): CheckpointStatusSchema | undefined {
  return checkpoints.find(
    (cp) => cp.service === service && cp.entity_type === entityType,
  );
}

function LastfmHistoryImport({
  checkpoints,
}: {
  checkpoints: CheckpointStatusSchema[];
}) {
  const [mode, setMode] = useState<ImportLastfmHistoryRequestMode>("recent");
  const operation = useImportOperation(
    useImportLastfmHistoryApiV1ImportsLastfmHistoryPost(),
    "Last.fm history import",
  );

  return (
    <OperationCard
      connector="lastfm"
      title="Scrobble History"
      description="Pull listening history from your Last.fm account."
      checkpoint={findCheckpoint(checkpoints, "lastfm", "plays")}
      operationId={operation.operationId}
      runId={operation.runId}
      operationType="import_lastfm_history"
      isPending={operation.isPending}
      onTrigger={() => operation.trigger({ data: { mode } })}
      syncTarget="lastfm:plays"
    >
      <div>
        <div
          className="inline-flex w-full rounded-lg bg-surface-sunken p-1"
          role="radiogroup"
          aria-label="Import mode"
          onKeyDown={(e) => {
            const options = ["recent", "incremental", "full"] as const;
            const idx = options.indexOf(mode);
            if (e.key === "ArrowRight" || e.key === "ArrowDown") {
              e.preventDefault();
              setMode(options[(idx + 1) % options.length]);
            } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
              e.preventDefault();
              setMode(options[(idx - 1 + options.length) % options.length]);
            }
          }}
        >
          {HISTORY_MODES.map((option) => (
            // biome-ignore lint/a11y/useSemanticElements: styled segmented control
            <button
              key={option.value}
              type="button"
              role="radio"
              aria-checked={mode === option.value}
              tabIndex={mode === option.value ? 0 : -1}
              onClick={() => setMode(option.value)}
              className={cn(
                "flex-1 rounded-md py-1.5 font-display text-xs font-medium transition-all duration-150",
                mode === option.value
                  ? "bg-surface-elevated text-text shadow-sm"
                  : "text-text-muted hover:text-text",
              )}
            >
              {option.label}
            </button>
          ))}
        </div>
        <p className="mt-2 text-xs text-text-faint">
          {HISTORY_MODES.find((m) => m.value === mode)?.desc}
        </p>
      </div>
    </OperationCard>
  );
}

function SpotifyLikesImport({
  checkpoints,
}: {
  checkpoints: CheckpointStatusSchema[];
}) {
  const [reimportAll, setReimportAll] = useState(false);
  const operation = useImportOperation(
    useImportSpotifyLikesApiV1ImportsSpotifyLikesPost(),
    "Spotify likes import",
  );
  const checkpoint = findCheckpoint(checkpoints, "spotify", "likes");

  const hasGap =
    checkpoint?.remote_total != null &&
    checkpoint?.local_count != null &&
    checkpoint.local_count < checkpoint.remote_total * 0.95;

  return (
    <OperationCard
      connector="spotify"
      title="Import Likes"
      description="Backup your Spotify liked tracks to the local database."
      checkpoint={checkpoint}
      operationId={operation.operationId}
      runId={operation.runId}
      operationType="import_spotify_likes"
      isPending={operation.isPending}
      onTrigger={() =>
        operation.trigger({ data: { force: reimportAll } }, () =>
          setReimportAll(false),
        )
      }
      syncTarget="spotify:likes"
    >
      {checkpoint?.local_count != null && (
        <p className="mt-2 font-mono text-xs text-text-muted">
          {hasGap
            ? `${formatCount(checkpoint.local_count)} of ${formatCount(checkpoint.remote_total ?? 0)} track${pluralSuffix(checkpoint.remote_total ?? 0)} imported`
            : `${formatCount(checkpoint.local_count)} track${pluralSuffix(checkpoint.local_count)} imported`}
        </p>
      )}
      {checkpoint != null && (
        <label
          htmlFor="spotify-reimport-all"
          className="mt-2 flex items-center gap-2"
        >
          <Switch
            id="spotify-reimport-all"
            size="sm"
            checked={reimportAll}
            onCheckedChange={setReimportAll}
          />
          <span className="font-body text-xs text-text-muted">
            Re-import entire library
          </span>
        </label>
      )}
    </OperationCard>
  );
}

function LastfmLikesExport({
  checkpoints,
}: {
  checkpoints: CheckpointStatusSchema[];
}) {
  const operation = useImportOperation(
    useExportLastfmLikesApiV1ImportsLastfmLikesPost(),
    "Last.fm likes export",
  );

  return (
    <OperationCard
      connector="lastfm"
      title="Export Loves"
      description="Love your liked tracks on Last.fm."
      checkpoint={findCheckpoint(checkpoints, "lastfm", "likes")}
      operationId={operation.operationId}
      runId={operation.runId}
      operationType="export_lastfm_likes"
      isPending={operation.isPending}
      triggerLabel="Export"
      onTrigger={() => operation.trigger({ data: {} })}
      syncTarget="lastfm:likes"
    />
  );
}

function SpotifyRecentImport({
  checkpoints,
}: {
  checkpoints: CheckpointStatusSchema[];
}) {
  const operation = useImportOperation(
    useImportSpotifyRecentApiV1ImportsSpotifyRecentPost(),
    "Spotify recent plays import",
  );

  return (
    <OperationCard
      connector="spotify"
      title="Spotify Recent Plays"
      description="Pull your latest listening straight from Spotify, so today's plays count today."
      // The API poll position, not the export's — this is the cursor the next
      // poll resumes from.
      checkpoint={findCheckpoint(checkpoints, "spotify", "plays")}
      operationId={operation.operationId}
      runId={operation.runId}
      operationType="import_spotify_recent"
      isPending={operation.isPending}
      onTrigger={() => operation.trigger({ data: {} })}
      syncTarget="spotify:plays"
    >
      <details className="mt-2 text-xs text-text-faint">
        <summary className="cursor-pointer hover:text-text-muted">
          Why only ~50 plays?
        </summary>
        <p className="mt-1 pl-3 border-l border-border">
          Spotify only keeps your 50 most recent plays available. Import
          regularly and nothing is missed — earlier history comes from Last.fm
          or a data export, and both merge into the same plays rather than
          duplicating them.
        </p>
      </details>
    </OperationCard>
  );
}

function AppleRecentImport({
  checkpoints,
}: {
  checkpoints: CheckpointStatusSchema[];
}) {
  const operation = useImportOperation(
    useImportAppleRecentApiV1ImportsAppleRecentPost(),
    "Apple Music recent plays import",
  );

  return (
    <OperationCard
      connector="apple_music"
      title="Apple Music Recent Plays"
      description="Poll Apple Music's recently-played feed for new listens."
      // The API poll position, not an export's — this is the fingerprint the
      // next poll resumes from.
      checkpoint={findCheckpoint(checkpoints, "apple", "plays")}
      operationId={operation.operationId}
      runId={operation.runId}
      operationType="import_apple_recent"
      isPending={operation.isPending}
      onTrigger={() => operation.trigger({ data: {} })}
      syncTarget="apple:plays"
    />
  );
}

function SpotifyHistoryImport() {
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const mutation = useImportSpotifyHistoryApiV1ImportsSpotifyHistoryPost();

  // The queue lives on the server; this query is how a reloaded tab re-attaches
  // to a drain in progress. Polling while any entry is unsettled is what
  // advances the per-file chips; a drained (or absent) queue stops it.
  const { data: queueData, refetch: refetchQueue } =
    useGetSpotifyHistoryQueueApiV1ImportsSpotifyHistoryQueueGet({
      query: {
        refetchInterval: (query) => {
          const res = query.state.data;
          if (res?.status !== 200) return false;
          return res.data.queue?.entries.some((entry) => !isSettled(entry))
            ? 2000
            : false;
        },
      },
    });
  // `queue: null` is the idle answer, not a missing resource — the GET never
  // 404s, so there is no error branch to render around.
  const queue = queueData?.status === 200 ? queueData.data.queue : null;
  const entries = queue?.entries ?? [];
  const queueActive = entries.some((entry) => !isSettled(entry));

  // Each file is a real run, so the global watcher would announce all thirteen.
  // An export is one piece of work, and its result is the manifest below.
  useEffect(() => {
    for (const entry of queue?.entries ?? []) {
      if (entry.run_id != null && isSettled(entry)) claimRunToast(entry.run_id);
    }
  }, [queue]);

  const cancelMutation =
    useCancelSpotifyHistoryQueueApiV1ImportsSpotifyHistoryQueueDelete({
      mutation: {
        meta: { errorLabel: "Failed to cancel queued files" },
      },
    });

  const trigger = () => {
    if (selectedFiles.length === 0) return;
    mutation.mutate(
      { data: { files: selectedFiles } },
      {
        onSuccess: (res) => {
          if (res.status === 200) {
            setSelectedFiles([]);
            // The POST registers the queue; one refetch makes the GET the only
            // description of it the page ever reads.
            void refetchQueue();
          } else {
            toasts.message("Failed to queue Spotify history import", {
              description: `Unexpected response (${res.status})`,
            });
          }
        },
        onError: (error: unknown) => {
          toasts.error("Failed to queue Spotify history import", error);
        },
      },
    );
  };

  return (
    <OperationCard
      connector="spotify"
      title="Spotify Data Export"
      description="Upload the streaming history JSON files from your Spotify privacy data download — the whole export at once."
      // No checkpoint: a file upload has no resume position. The
      // ("spotify","plays") checkpoint now tracks the API poll cursor and is
      // shown on the Recent Plays card instead.
      checkpoint={undefined}
      // ONE id for the whole export: deriving it from the running file meant
      // re-attaching at every handover, attached to nothing in between. Dropped
      // once the queue settles, or the card waits forever on a retired stream
      // and refuses the next upload.
      operationId={queueActive ? (queue?.operation_id ?? null) : null}
      // The drain writes no audit row; the durable record is one per file.
      runId={null}
      operationType="import_spotify_history"
      isPending={mutation.isPending}
      onTrigger={trigger}
      triggerDisabled={selectedFiles.length === 0 || queueActive}
      triggerLabel={
        selectedFiles.length > 1
          ? `Import ${selectedFiles.length} files`
          : "Import"
      }
      hideProgressBar
      renderDetail={(progress) =>
        entries.length > 0 ? (
          <ImportQueueManifest
            entries={entries}
            subOperation={progress?.subOperation ?? null}
            onCancelRemaining={() => cancelMutation.mutate()}
            cancelDisabled={cancelMutation.isPending}
          />
        ) : null
      }
    >
      <FileUpload
        // Remount when a queue (re)starts: FileUpload owns its selected-file
        // display, so the parent clearing its copy after a successful upload
        // leaves stale filenames on screen otherwise.
        key={queue?.queue_id ?? "no-queue"}
        accept=".json"
        onFilesSelect={setSelectedFiles}
        disabled={mutation.isPending || queueActive}
      />
      <details className="mt-2 text-xs text-text-faint">
        <summary className="cursor-pointer hover:text-text-muted">
          How to get your data export
        </summary>
        <p className="mt-1 pl-3 border-l border-border">
          Go to spotify.com/account/privacy &rarr; Request your data &rarr;
          Upload every streaming history JSON file here in one go.
        </p>
      </details>
    </OperationCard>
  );
}

// ─── Page ───────────────────────────────────────────────────────

export function Sync() {
  const { data, isError, error } =
    useGetCheckpointsApiV1ImportsCheckpointsGet();
  const checkpoints = data?.status === 200 ? data.data : [];

  return (
    <div>
      <title>Sync — Mixd</title>
      <PageHeader
        title="Sync"
        description="Import and sync your music data across services."
      />

      {isError && isDatabaseUnavailable(error) ? (
        <DatabaseUnavailable />
      ) : (
        <>
          {isError && (
            <div
              role="alert"
              className="mb-6 flex items-center gap-2 rounded-lg border border-status-expired/30 bg-status-expired/5 px-4 py-2.5 text-sm text-status-expired"
            >
              <AlertTriangle className="size-4 shrink-0" />
              <span>
                Couldn&apos;t load sync history. Timestamps may be unavailable.
              </span>
            </div>
          )}

          <div className="space-y-12">
            {/* ── Listening History ──────────────────────── */}
            <section className="space-y-3">
              <SectionHeader
                title="Listening History"
                description="Your play counts across services — scrobbles, stream history, and data exports."
              />
              <div className="space-y-3">
                <div
                  className="animate-fade-up"
                  style={{ animationDelay: "0ms" }}
                >
                  <LastfmHistoryImport checkpoints={checkpoints} />
                </div>
                <div
                  className="animate-fade-up"
                  style={{ animationDelay: "75ms" }}
                >
                  <SpotifyRecentImport checkpoints={checkpoints} />
                </div>
                <div
                  className="animate-fade-up"
                  style={{ animationDelay: "150ms" }}
                >
                  <SpotifyHistoryImport />
                </div>
                <div
                  className="animate-fade-up"
                  style={{ animationDelay: "225ms" }}
                >
                  <AppleRecentImport checkpoints={checkpoints} />
                </div>
              </div>
            </section>

            {/* ── Liked Tracks ──────────────────────────── */}
            <section className="space-y-3">
              <SectionHeader
                title="Liked Tracks"
                description="Tracks you've hearted or loved — sync between Spotify and Last.fm."
              />
              <div className="space-y-3">
                <div
                  className="animate-fade-up"
                  style={{ animationDelay: "0ms" }}
                >
                  <SpotifyLikesImport checkpoints={checkpoints} />
                </div>
                <div
                  className="animate-fade-up"
                  style={{ animationDelay: "75ms" }}
                >
                  <LastfmLikesExport checkpoints={checkpoints} />
                </div>
              </div>
            </section>
          </div>
        </>
      )}
    </div>
  );
}
