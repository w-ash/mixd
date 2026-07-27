import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle } from "lucide-react";
import type React from "react";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router";
import {
  getGetConnectorPlayPollingApiV1ConnectorsServicePlayPollingGetQueryKey,
  useGetConnectorPlayPollingApiV1ConnectorsServicePlayPollingGet,
  useGetConnectorsApiV1ConnectorsGet,
  useSetConnectorPlayPollingApiV1ConnectorsServicePlayPollingPut,
} from "#/api/generated/connectors/connectors";
import {
  getGetCheckpointsApiV1ImportsCheckpointsGetQueryKey,
  useExportLastfmLikesApiV1ImportsLastfmLikesPost,
  useGetCheckpointsApiV1ImportsCheckpointsGet,
  useImportLastfmHistoryApiV1ImportsLastfmHistoryPost,
  useImportSpotifyHistoryApiV1ImportsSpotifyHistoryPost,
  useImportSpotifyLikesApiV1ImportsSpotifyLikesPost,
  useImportSpotifyRecentApiV1ImportsSpotifyRecentPost,
} from "#/api/generated/imports/imports";
import type {
  CheckpointStatusSchema,
  ImportLastfmHistoryRequestMode,
  OperationStartedResponse,
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
import { useOperationProgress } from "#/hooks/useOperationProgress";
import { useSyncScheduleController } from "#/hooks/useScheduleController";
import { formatDateTime } from "#/lib/format";
import { pluralSuffix } from "#/lib/pluralize";
import { describeMinutes, type SyncTarget } from "#/lib/schedule";
import { type RunOperationType, toasts } from "#/lib/toasts";
import { cn } from "#/lib/utils";

/** Query keys to invalidate when an import operation completes. */
const CHECKPOINT_KEYS = [
  getGetCheckpointsApiV1ImportsCheckpointsGetQueryKey(),
] as const;

/** Build shared onSuccess/onError callbacks for import mutation triggers. */
function makeOperationCallbacks(
  label: string,
  setOperationId: (id: string) => void,
  setRunId: (id: string | null) => void,
) {
  return {
    onSuccess: (res: { status: number; data: unknown }) => {
      if (res.status === 200) {
        const data = res.data as OperationStartedResponse;
        setOperationId(data.operation_id);
        setRunId(data.run_id ?? null);
      } else {
        toasts.message(`Failed to start ${label}`, {
          description: `Unexpected response (${res.status})`,
        });
      }
    },
    onError: (error: unknown) => {
      toasts.error(`Failed to start ${label}`, error);
    },
  };
}

// ─── Operation Card ──────────────────────────────────────────────

/**
 * Recurring-schedule control for a sync target, sharing the workflow schedule
 * shell. Only the three background-syncable targets pass one in; the file-upload
 * imports (which can't run unattended) render no scheduler.
 */
function SyncScheduleField({ targetId }: { targetId: SyncTarget }) {
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

interface OperationCardProps {
  connector: string;
  title: string;
  description: string;
  checkpoint: CheckpointStatusSchema | undefined;
  operationId: string | null;
  runId: string | null;
  operationType: RunOperationType;
  /** When false, the trigger is disabled with a "connect first" hint. Omit for
   * flows with no live connector (e.g. the Spotify GDPR file upload). */
  connected?: boolean;
  isPending: boolean;
  onTrigger: () => void;
  triggerLabel?: string;
  triggerDisabled?: boolean;
  /** Background-sync target id (e.g. `lastfm:plays`). When set, the card shows a recurring-schedule control. */
  syncTarget?: SyncTarget;
  /** Self-managed poll target (`spotify:plays`). Shows a read-only cadence plus
   * an on/off switch instead of the editable picker — see `PlayPollingField`. */
  pollingService?: string;
  children?: React.ReactNode;
}

function OperationCard({
  connector,
  title,
  description,
  checkpoint,
  operationId,
  runId,
  operationType,
  connected,
  isPending,
  onTrigger,
  triggerLabel = "Import",
  triggerDisabled,
  syncTarget,
  pollingService,
  children,
}: OperationCardProps) {
  const { progress, isActive } = useOperationProgress(operationId, {
    invalidateKeys: CHECKPOINT_KEYS,
  });
  const navigate = useNavigate();
  const toastedForOpIdRef = useRef<string | null>(null);

  useEffect(() => {
    if (operationId === null || progress === null) return;
    const isTerminal =
      progress.status === "completed" ||
      progress.status === "failed" ||
      progress.status === "cancelled";
    if (!isTerminal) return;
    if (toastedForOpIdRef.current === operationId) return;
    toastedForOpIdRef.current = operationId;

    toasts.runCompleted({
      operationType,
      counts: progress.counts ?? {},
      issueCount: 0,
      runId,
      failed: progress.status !== "completed",
      onNavigate: navigate,
    });
  }, [operationId, progress, runId, operationType, navigate]);

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
            isPending || isActive || triggerDisabled || connected === false
          }
          onClick={onTrigger}
          className="self-start lg:self-auto"
        >
          {isPending ? "Starting..." : isActive ? "Running..." : triggerLabel}
        </Button>
      </div>

      {connected === false && (
        <p className="mt-2 text-xs text-text-faint">
          Connect {connector} in Integrations to enable this.
        </p>
      )}

      {children && <div className="mt-3">{children}</div>}

      {progress && <OperationProgress progress={progress} className="mt-3" />}

      {syncTarget && <SyncScheduleField targetId={syncTarget} />}

      {pollingService && <PlayPollingField service={pollingService} />}

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
 * Deliberately not the shared `SchedulePicker`. There is one schedule row per
 * (user, target), and this one's interval is rewritten by the poller itself
 * after every poll — so offering the daily/weekly editor would let a save
 * overwrite the adaptive cadence and silently switch the backoff off. The
 * backend enforces the same rule by keeping this target out of
 * `USER_SCHEDULABLE_TARGETS`, which makes its upsert route 400.
 */
function PlayPollingField({ service }: { service: string }) {
  const queryClient = useQueryClient();
  const { data, isLoading } =
    useGetConnectorPlayPollingApiV1ConnectorsServicePlayPollingGet(service, {
      query: { staleTime: STALE.SLOW, retry: false },
    });
  const state = data?.status === 200 ? data.data : null;

  const toggle = useSetConnectorPlayPollingApiV1ConnectorsServicePlayPollingPut(
    {
      mutation: {
        onSuccess: () => {
          void queryClient.invalidateQueries({
            queryKey:
              getGetConnectorPlayPollingApiV1ConnectorsServicePlayPollingGetQueryKey(
                service,
              ),
          });
        },
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
  connected,
}: {
  checkpoints: CheckpointStatusSchema[];
  connected: boolean;
}) {
  const [operationId, setOperationId] = useState<string | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [mode, setMode] = useState<ImportLastfmHistoryRequestMode>("recent");
  const mutation = useImportLastfmHistoryApiV1ImportsLastfmHistoryPost();

  const trigger = () => {
    mutation.mutate(
      { data: { mode } },
      makeOperationCallbacks(
        "Last.fm history import",
        setOperationId,
        setRunId,
      ),
    );
  };

  return (
    <OperationCard
      connector="lastfm"
      title="Scrobble History"
      description="Pull listening history from your Last.fm account."
      checkpoint={findCheckpoint(checkpoints, "lastfm", "plays")}
      operationId={operationId}
      runId={runId}
      operationType="import_lastfm_history"
      connected={connected}
      isPending={mutation.isPending}
      onTrigger={trigger}
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
  connected,
}: {
  checkpoints: CheckpointStatusSchema[];
  connected: boolean;
}) {
  const [operationId, setOperationId] = useState<string | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [reimportAll, setReimportAll] = useState(false);
  const mutation = useImportSpotifyLikesApiV1ImportsSpotifyLikesPost();
  const checkpoint = findCheckpoint(checkpoints, "spotify", "likes");

  const hasGap =
    checkpoint?.remote_total != null &&
    checkpoint?.local_count != null &&
    checkpoint.local_count < checkpoint.remote_total * 0.95;

  const trigger = () => {
    const callbacks = makeOperationCallbacks(
      "Spotify likes import",
      setOperationId,
      setRunId,
    );
    mutation.mutate(
      { data: { force: reimportAll } },
      {
        ...callbacks,
        onSuccess: (res) => {
          setReimportAll(false);
          callbacks.onSuccess(res);
        },
      },
    );
  };

  return (
    <OperationCard
      connector="spotify"
      title="Import Likes"
      description="Backup your Spotify liked tracks to the local database."
      checkpoint={checkpoint}
      operationId={operationId}
      runId={runId}
      operationType="import_spotify_likes"
      connected={connected}
      isPending={mutation.isPending}
      onTrigger={trigger}
      syncTarget="spotify:likes"
    >
      {checkpoint?.local_count != null && (
        <p className="mt-2 font-mono text-xs text-text-muted">
          {hasGap
            ? `${checkpoint.local_count.toLocaleString()} of ${checkpoint.remote_total?.toLocaleString()} track${pluralSuffix(checkpoint.remote_total ?? 0)} imported`
            : `${checkpoint.local_count.toLocaleString()} track${pluralSuffix(checkpoint.local_count)} imported`}
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
  connected,
}: {
  checkpoints: CheckpointStatusSchema[];
  connected: boolean;
}) {
  const [operationId, setOperationId] = useState<string | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const mutation = useExportLastfmLikesApiV1ImportsLastfmLikesPost();

  const trigger = () => {
    mutation.mutate(
      { data: {} },
      makeOperationCallbacks("Last.fm likes export", setOperationId, setRunId),
    );
  };

  return (
    <OperationCard
      connector="lastfm"
      title="Export Loves"
      description="Love your liked tracks on Last.fm."
      checkpoint={findCheckpoint(checkpoints, "lastfm", "likes")}
      operationId={operationId}
      runId={runId}
      operationType="export_lastfm_likes"
      connected={connected}
      isPending={mutation.isPending}
      triggerLabel="Export"
      onTrigger={trigger}
      syncTarget="lastfm:likes"
    />
  );
}

function SpotifyRecentImport({
  checkpoints,
  needsReconnect,
}: {
  checkpoints: CheckpointStatusSchema[];
  needsReconnect: boolean;
}) {
  const [operationId, setOperationId] = useState<string | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const mutation = useImportSpotifyRecentApiV1ImportsSpotifyRecentPost();

  const trigger = () => {
    mutation.mutate(
      { data: {} },
      makeOperationCallbacks(
        "Spotify recent plays import",
        setOperationId,
        setRunId,
      ),
    );
  };

  return (
    <OperationCard
      connector="spotify"
      title="Spotify Recent Plays"
      description="Pull your latest listening straight from Spotify, so today's plays count today."
      // The API poll position, not the export's — this is the cursor the next
      // poll resumes from.
      checkpoint={findCheckpoint(checkpoints, "spotify", "plays")}
      operationId={operationId}
      runId={runId}
      operationType="import_spotify_recent"
      isPending={mutation.isPending}
      onTrigger={trigger}
      // A pre-v0.10.1 grant reports connected (likes and playlists still work),
      // so the shared connected gate can't catch this one.
      triggerDisabled={needsReconnect}
      pollingService="spotify"
    >
      {needsReconnect && (
        <p className="mt-2 text-xs text-status-expired">
          Re-connect Spotify in Integrations to grant listening-history access.
        </p>
      )}
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

function SpotifyHistoryImport() {
  const [operationId, setOperationId] = useState<string | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const mutation = useImportSpotifyHistoryApiV1ImportsSpotifyHistoryPost();

  const trigger = () => {
    if (!selectedFile) return;
    mutation.mutate(
      { data: { file: selectedFile } },
      makeOperationCallbacks(
        "Spotify history import",
        setOperationId,
        setRunId,
      ),
    );
  };

  return (
    <OperationCard
      connector="spotify"
      title="Spotify Data Export"
      description="Upload the streaming history JSON from your Spotify privacy data download."
      // No checkpoint: a file upload has no resume position. The
      // ("spotify","plays") checkpoint now tracks the API poll cursor and is
      // shown on the Recent Plays card instead.
      checkpoint={undefined}
      operationId={operationId}
      runId={runId}
      operationType="import_spotify_history"
      isPending={mutation.isPending}
      onTrigger={trigger}
      triggerDisabled={!selectedFile}
    >
      <FileUpload
        accept=".json"
        onFileSelect={setSelectedFile}
        disabled={mutation.isPending}
      />
      <details className="mt-2 text-xs text-text-faint">
        <summary className="cursor-pointer hover:text-text-muted">
          How to get your data export
        </summary>
        <p className="mt-1 pl-3 border-l border-border">
          Go to spotify.com/account/privacy &rarr; Request your data &rarr;
          Upload the streaming history JSON file here.
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

  // Gate the per-connector triggers on connected state (the backend 409s anyway —
  // this is the friendlier pre-emptive disable). Optimistic during load: default
  // connected so a slow status query doesn't flicker every button disabled.
  const { data: connectorsData } = useGetConnectorsApiV1ConnectorsGet();
  const connectedByName: Record<string, boolean> = {};
  const authErrorByName: Record<string, string | null | undefined> = {};
  if (connectorsData?.status === 200) {
    for (const c of connectorsData.data) {
      connectedByName[c.name] = c.connected;
      authErrorByName[c.name] = c.auth_error;
    }
  }
  const lastfmConnected = connectedByName.lastfm ?? true;
  const spotifyConnected = connectedByName.spotify ?? true;
  // scope_missing ships with connected=true — likes and playlists still work,
  // so only the surface that needs the new scope gates on it.
  const spotifyNeedsReconnect = authErrorByName.spotify === "scope_missing";

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
                  <LastfmHistoryImport
                    checkpoints={checkpoints}
                    connected={lastfmConnected}
                  />
                </div>
                <div
                  className="animate-fade-up"
                  style={{ animationDelay: "75ms" }}
                >
                  <SpotifyRecentImport
                    checkpoints={checkpoints}
                    needsReconnect={spotifyNeedsReconnect}
                  />
                </div>
                <div
                  className="animate-fade-up"
                  style={{ animationDelay: "150ms" }}
                >
                  <SpotifyHistoryImport />
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
                  <SpotifyLikesImport
                    checkpoints={checkpoints}
                    connected={spotifyConnected}
                  />
                </div>
                <div
                  className="animate-fade-up"
                  style={{ animationDelay: "75ms" }}
                >
                  <LastfmLikesExport
                    checkpoints={checkpoints}
                    connected={lastfmConnected}
                  />
                </div>
              </div>
            </section>
          </div>
        </>
      )}
    </div>
  );
}
