import { AlertTriangle } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import {
  getGetCheckpointsApiV1ImportsCheckpointsGetQueryKey,
  useExportLastfmLikesApiV1ImportsLastfmLikesPost,
  useGetCheckpointsApiV1ImportsCheckpointsGet,
  useImportLastfmHistoryApiV1ImportsLastfmHistoryPost,
  useImportSpotifyHistoryApiV1ImportsSpotifyHistoryPost,
  useImportSpotifyLikesApiV1ImportsSpotifyLikesPost,
} from "#/api/generated/imports/imports";
import type {
  CheckpointStatusSchema,
  ImportLastfmHistoryRequestMode,
  OperationStartedResponse,
} from "#/api/generated/model";
import { PageHeader } from "#/components/layout/PageHeader";
import {
  ConnectorIcon,
  type ConnectorName,
} from "#/components/shared/ConnectorIcon";
import {
  DatabaseUnavailable,
  isDatabaseUnavailable,
} from "#/components/shared/DatabaseUnavailable";
import { FileUpload } from "#/components/shared/FileUpload";
import { OperationProgress } from "#/components/shared/OperationProgress";
import { SectionHeader } from "#/components/shared/SectionHeader";
import { Button } from "#/components/ui/button";
import { Switch } from "#/components/ui/switch";
import { useOperationProgress } from "#/hooks/useOperationProgress";
import { formatDateTime } from "#/lib/format";
import { cn } from "#/lib/utils";

/** Query keys to invalidate when an import operation completes. */
const CHECKPOINT_KEYS = [
  getGetCheckpointsApiV1ImportsCheckpointsGetQueryKey(),
] as const;

/** Build shared onSuccess/onError callbacks for import mutation triggers. */
function makeOperationCallbacks(
  label: string,
  setOperationId: (id: string) => void,
) {
  return {
    onSuccess: (res: { status: number; data: unknown }) => {
      if (res.status === 200) {
        setOperationId((res.data as OperationStartedResponse).operation_id);
      } else {
        toast.error(`Failed to start ${label}`, {
          description: `Unexpected response (${res.status})`,
        });
      }
    },
    onError: (error: unknown) => {
      toast.error(`Failed to start ${label}`, {
        description: error instanceof Error ? error.message : "Unknown error",
      });
    },
  };
}

// ─── Operation Card ──────────────────────────────────────────────

interface OperationCardProps {
  connector: ConnectorName;
  title: string;
  description: string;
  checkpoint: CheckpointStatusSchema | undefined;
  operationId: string | null;
  isPending: boolean;
  onTrigger: () => void;
  triggerLabel?: string;
  triggerDisabled?: boolean;
  children?: React.ReactNode;
}

function OperationCard({
  connector,
  title,
  description,
  checkpoint,
  operationId,
  isPending,
  onTrigger,
  triggerLabel = "Import",
  triggerDisabled,
  children,
}: OperationCardProps) {
  const { progress, isActive } = useOperationProgress(operationId, {
    invalidateKeys: CHECKPOINT_KEYS,
  });

  return (
    <div className="rounded-xl border border-border bg-surface-elevated shadow-elevated p-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <ConnectorIcon name={connector} />
            <h3 className="font-display text-base font-semibold">{title}</h3>
          </div>
          <p className="mt-0.5 text-sm text-text-muted">{description}</p>
        </div>
        <Button
          size="sm"
          disabled={isPending || isActive || triggerDisabled}
          onClick={onTrigger}
        >
          {isPending ? "Starting..." : isActive ? "Running..." : triggerLabel}
        </Button>
      </div>

      {children && <div className="mt-3">{children}</div>}

      {progress && <OperationProgress progress={progress} className="mt-3" />}

      <p className="mt-3 text-right text-xs text-text-faint">
        Last sync:{" "}
        <span className="font-mono text-text-muted">
          {formatDateTime(checkpoint?.last_sync_timestamp)}
        </span>
      </p>
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
  const [operationId, setOperationId] = useState<string | null>(null);
  const [mode, setMode] = useState<ImportLastfmHistoryRequestMode>("recent");
  const mutation = useImportLastfmHistoryApiV1ImportsLastfmHistoryPost();

  const trigger = () => {
    mutation.mutate(
      { data: { mode } },
      makeOperationCallbacks("Last.fm history import", setOperationId),
    );
  };

  return (
    <OperationCard
      connector="lastfm"
      title="Scrobble History"
      description="Pull listening history from your Last.fm account."
      checkpoint={findCheckpoint(checkpoints, "lastfm", "plays")}
      operationId={operationId}
      isPending={mutation.isPending}
      onTrigger={trigger}
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
  const [operationId, setOperationId] = useState<string | null>(null);
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
      isPending={mutation.isPending}
      onTrigger={trigger}
    >
      {checkpoint?.local_count != null && (
        <p className="mt-2 font-mono text-xs text-text-muted">
          {hasGap
            ? `${checkpoint.local_count.toLocaleString()} of ${checkpoint.remote_total?.toLocaleString()} tracks imported`
            : `${checkpoint.local_count.toLocaleString()} tracks imported`}
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
  const [operationId, setOperationId] = useState<string | null>(null);
  const mutation = useExportLastfmLikesApiV1ImportsLastfmLikesPost();

  const trigger = () => {
    mutation.mutate(
      { data: {} },
      makeOperationCallbacks("Last.fm likes export", setOperationId),
    );
  };

  return (
    <OperationCard
      connector="lastfm"
      title="Export Loves"
      description="Love your liked tracks on Last.fm."
      checkpoint={findCheckpoint(checkpoints, "lastfm", "likes")}
      operationId={operationId}
      isPending={mutation.isPending}
      triggerLabel="Export"
      onTrigger={trigger}
    />
  );
}

function SpotifyHistoryImport({
  checkpoints,
}: {
  checkpoints: CheckpointStatusSchema[];
}) {
  const [operationId, setOperationId] = useState<string | null>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const mutation = useImportSpotifyHistoryApiV1ImportsSpotifyHistoryPost();

  const trigger = () => {
    if (!selectedFile) return;
    mutation.mutate(
      { data: { file: selectedFile } },
      makeOperationCallbacks("Spotify history import", setOperationId),
    );
  };

  return (
    <OperationCard
      connector="spotify"
      title="Spotify Data Export"
      description="Upload the streaming history JSON from your Spotify privacy data download."
      checkpoint={findCheckpoint(checkpoints, "spotify", "plays")}
      operationId={operationId}
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
                  <SpotifyHistoryImport checkpoints={checkpoints} />
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
