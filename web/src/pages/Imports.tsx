import { useState } from "react";
import { toast } from "sonner";

import {
  getGetCheckpointsApiV1ImportsCheckpointsGetQueryKey,
  useExportLastfmLikesApiV1ImportsLastfmLikesPost,
  useGetCheckpointsApiV1ImportsCheckpointsGet,
  useImportLastfmHistoryApiV1ImportsLastfmHistoryPost,
  useImportSpotifyHistoryApiV1ImportsSpotifyHistoryPost,
  useImportSpotifyLikesApiV1ImportsSpotifyLikesPost,
} from "@/api/generated/imports/imports";
import type {
  CheckpointStatusSchema,
  ImportLastfmHistoryRequestMode,
} from "@/api/generated/model";
import { PageHeader } from "@/components/layout/PageHeader";
import { FileUpload } from "@/components/shared/FileUpload";
import { OperationProgress } from "@/components/shared/OperationProgress";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useOperationProgress } from "@/hooks/useOperationProgress";
import { formatDateTime } from "@/lib/format";

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
        // Safe assertion: 200 responses always carry OperationStartedResponse
        setOperationId((res.data as { operation_id: string }).operation_id);
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

// ─── Import Card ────────────────────────────────────────────────

interface ImportCardProps {
  title: string;
  description: string;
  checkpoint: CheckpointStatusSchema | undefined;
  operationId: string | null;
  isPending: boolean;
  onTrigger: () => void;
  children?: React.ReactNode;
}

function ImportCard({
  title,
  description,
  checkpoint,
  operationId,
  isPending,
  onTrigger,
  children,
}: ImportCardProps) {
  const { progress } = useOperationProgress(operationId, {
    invalidateKeys: CHECKPOINT_KEYS,
  });
  const isActive =
    progress?.status === "running" || progress?.status === "pending";

  return (
    <Card className="p-5 space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="font-display text-sm font-semibold">{title}</h3>
          <p className="text-xs text-text-muted mt-0.5">{description}</p>
        </div>
        <Button size="sm" disabled={isPending || isActive} onClick={onTrigger}>
          {isPending ? "Starting..." : isActive ? "Running..." : "Run"}
        </Button>
      </div>

      {/* Extra controls (mode selector, file upload, etc.) */}
      {children}

      {/* Progress display */}
      {progress && <OperationProgress progress={progress} />}

      {/* Last sync info */}
      <div className="text-xs text-text-faint">
        Last sync: {formatDateTime(checkpoint?.last_sync_timestamp)}
      </div>
    </Card>
  );
}

// ─── Import Operations ──────────────────────────────────────────

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
    <ImportCard
      title="Import Last.fm History"
      description="Pull listening history from your Last.fm account."
      checkpoint={findCheckpoint(checkpoints, "lastfm", "plays")}
      operationId={operationId}
      isPending={mutation.isPending}
      onTrigger={trigger}
    >
      <div className="flex items-center gap-2">
        <label className="text-xs text-text-muted" htmlFor="lastfm-mode">
          Mode:
        </label>
        <select
          id="lastfm-mode"
          value={mode}
          onChange={(e) =>
            setMode(e.target.value as ImportLastfmHistoryRequestMode)
          }
          className="rounded border border-border bg-surface-elevated px-2 py-1 text-xs text-text focus:outline-none focus:ring-1 focus:ring-primary"
        >
          <option value="recent">Recent</option>
          <option value="incremental">Incremental</option>
          <option value="full">Full</option>
        </select>
      </div>
    </ImportCard>
  );
}

function SpotifyLikesImport({
  checkpoints,
}: {
  checkpoints: CheckpointStatusSchema[];
}) {
  const [operationId, setOperationId] = useState<string | null>(null);
  const mutation = useImportSpotifyLikesApiV1ImportsSpotifyLikesPost();

  const trigger = () => {
    mutation.mutate(
      { data: {} },
      makeOperationCallbacks("Spotify likes import", setOperationId),
    );
  };

  return (
    <ImportCard
      title="Import Spotify Likes"
      description="Backup your Spotify liked tracks to the local database."
      checkpoint={findCheckpoint(checkpoints, "spotify", "likes")}
      operationId={operationId}
      isPending={mutation.isPending}
      onTrigger={trigger}
    />
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
    <ImportCard
      title="Export Likes to Last.fm"
      description="Love your liked tracks on Last.fm."
      checkpoint={findCheckpoint(checkpoints, "lastfm", "likes")}
      operationId={operationId}
      isPending={mutation.isPending}
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
    <ImportCard
      title="Import Spotify History"
      description="Upload your Spotify GDPR data export (JSON)."
      checkpoint={findCheckpoint(checkpoints, "spotify", "plays")}
      operationId={operationId}
      isPending={mutation.isPending}
      onTrigger={trigger}
    >
      <FileUpload
        accept=".json"
        onFileSelect={setSelectedFile}
        disabled={mutation.isPending}
      />
    </ImportCard>
  );
}

// ─── Checkpoint Overview ────────────────────────────────────────

function CheckpointStatus({
  checkpoints,
  isLoading,
}: {
  checkpoints: CheckpointStatusSchema[];
  isLoading: boolean;
}) {
  if (isLoading) {
    return (
      <div className="grid gap-2 sm:grid-cols-2">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton
            // biome-ignore lint/suspicious/noArrayIndexKey: static skeleton
            key={i}
            className="h-14 rounded-lg"
          />
        ))}
      </div>
    );
  }

  if (checkpoints.length === 0) {
    return (
      <p className="text-sm text-text-muted">
        No sync history yet — run an import above to get started.
      </p>
    );
  }

  return (
    <div className="grid gap-2 sm:grid-cols-2">
      {checkpoints.map((cp) => (
        <div
          key={`${cp.service}-${cp.entity_type}`}
          className="flex items-center justify-between rounded-lg border border-border-muted bg-surface-sunken px-4 py-3"
        >
          <div>
            <span className="text-xs font-medium text-text capitalize">
              {cp.service}
            </span>
            <span className="text-xs text-text-faint ml-1.5">
              {cp.entity_type}
            </span>
          </div>
          <span className="text-xs text-text-muted font-mono">
            {cp.has_previous_sync
              ? formatDateTime(cp.last_sync_timestamp)
              : "Never synced"}
          </span>
        </div>
      ))}
    </div>
  );
}

// ─── Page ───────────────────────────────────────────────────────

export function Imports() {
  const { data, isLoading } = useGetCheckpointsApiV1ImportsCheckpointsGet();
  const checkpoints = data?.status === 200 ? data.data : [];

  return (
    <div>
      <PageHeader
        title="Imports"
        description="Import and sync your music data across services."
      />

      <div className="space-y-8">
        {/* Import operations */}
        <section>
          <h2 className="font-display text-lg font-semibold mb-3">
            Operations
          </h2>
          <div className="grid gap-3 lg:grid-cols-2">
            <LastfmHistoryImport checkpoints={checkpoints} />
            <SpotifyLikesImport checkpoints={checkpoints} />
            <LastfmLikesExport checkpoints={checkpoints} />
            <SpotifyHistoryImport checkpoints={checkpoints} />
          </div>
        </section>

        {/* Checkpoint overview */}
        <section>
          <h2 className="font-display text-lg font-semibold mb-3">
            Sync Status
          </h2>
          <CheckpointStatus checkpoints={checkpoints} isLoading={isLoading} />
        </section>
      </div>
    </div>
  );
}
