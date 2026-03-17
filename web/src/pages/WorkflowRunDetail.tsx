import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  HelpCircle,
  Play,
} from "lucide-react";
import { useCallback, useState } from "react";
import { useParams } from "react-router";

import type { WorkflowRunNodeSchema } from "@/api/generated/model";
import {
  useGetWorkflowApiV1WorkflowsWorkflowIdGet,
  useGetWorkflowRunApiV1WorkflowsWorkflowIdRunsRunIdGet,
} from "@/api/generated/workflows/workflows";
import { PageHeader } from "@/components/layout/PageHeader";
import { BackLink } from "@/components/shared/BackLink";
import { EmptyState } from "@/components/shared/EmptyState";
import {
  getStatusConfig,
  RunStatusBadge,
} from "@/components/shared/RunStatusBadge";
import { SectionHeader } from "@/components/shared/SectionHeader";
import { WorkflowGraph } from "@/components/shared/WorkflowGraph";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useWorkflowExecution } from "@/hooks/useWorkflowExecution";
import {
  formatDate,
  formatDuration,
  formatMetricHeader,
  formatMetricValue,
} from "@/lib/format";
import type { NodeStatus } from "@/lib/sse-types";
import { cn } from "@/lib/utils";
import {
  getNodeCategory,
  type PlaylistChanges,
  type PlaylistChangeTrack,
} from "@/lib/workflow-config";

// --- Sub-components ---

function RunDetailSkeleton() {
  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-4 w-96" />
      </div>
      <Skeleton className="h-[700px] w-full rounded-lg" />
    </div>
  );
}

/** Expandable panel showing playlist changes for destination nodes. */
function PlaylistChangesPanel({ node }: { node: WorkflowRunNodeSchema }) {
  const changes = node.node_details?.playlist_changes as
    | PlaylistChanges
    | undefined;
  if (!changes) return null;

  return (
    <div className="mt-3 space-y-3">
      {changes.tracks_removed.length > 0 && (
        <TrackChangeGroup
          label="Removed from playlist"
          tracks={changes.tracks_removed}
          total={changes.tracks_removed_total}
          className="text-destructive/80"
        />
      )}
      {changes.tracks_added.length > 0 && (
        <TrackChangeGroup
          label="Added to playlist"
          tracks={changes.tracks_added}
          total={changes.tracks_added_total}
          className="text-status-connected/80"
        />
      )}
      {changes.tracks_moved > 0 && (
        <p className="px-2 text-xs text-text-muted">
          {changes.tracks_moved} track{changes.tracks_moved !== 1 ? "s" : ""}{" "}
          reordered
        </p>
      )}
    </div>
  );
}

function TrackChangeGroup({
  label,
  tracks,
  total,
  className,
}: {
  label: string;
  tracks: PlaylistChangeTrack[];
  total?: number;
  className?: string;
}) {
  const actualTotal = total ?? tracks.length;
  const remaining = actualTotal - tracks.length;

  return (
    <div>
      <p className={cn("mb-1 font-display text-xs font-medium", className)}>
        {label} ({actualTotal})
      </p>
      <div className="space-y-px">
        {tracks.map((t) => (
          <div
            key={t.track_id}
            className="flex items-baseline gap-3 rounded px-2 py-1 text-xs hover:bg-surface-sunken/50"
          >
            <span className="min-w-0 truncate text-text">{t.title}</span>
            <span className="shrink-0 text-text-faint">{t.artists}</span>
          </div>
        ))}
        {remaining > 0 && (
          <p className="px-2 py-1 text-xs text-text-muted">
            and {remaining} more
          </p>
        )}
      </div>
    </div>
  );
}

/** Single node row with expand/collapse for details. */
function NodeExecutionRow({ node }: { node: WorkflowRunNodeSchema }) {
  const [expanded, setExpanded] = useState(false);
  const hasDetails = Boolean(node.node_details?.playlist_changes);

  const categoryConfig = getNodeCategory(node.node_type);

  const toggle = useCallback(() => {
    if (hasDetails) setExpanded((prev) => !prev);
  }, [hasDetails]);

  const containerClass = cn(
    "rounded-lg border border-border bg-surface-elevated px-4 py-3",
    hasDetails && "cursor-pointer",
  );

  const interactiveProps = hasDetails
    ? {
        onClick: toggle,
        onKeyDown: (e: React.KeyboardEvent) => {
          if (e.key === "Enter" || e.key === " ") toggle();
        },
        role: "button" as const,
        tabIndex: 0,
      }
    : {};

  return (
    <div className={containerClass} {...interactiveProps}>
      <div className="flex items-center gap-4">
        <span className="font-mono text-xs text-text-faint w-6">
          {node.execution_order}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="font-display text-sm font-medium text-text">
              {node.node_id}
            </span>
            <span
              className="rounded-full px-1.5 py-0.5 text-[10px] font-display"
              style={{
                backgroundColor: `color-mix(in oklch, ${categoryConfig.accentColor} 20%, transparent)`,
                color: categoryConfig.accentColor,
              }}
            >
              {categoryConfig.label}
            </span>
          </div>
          {node.error_message && (
            <p className="mt-0.5 text-xs text-destructive truncate">
              {node.error_message}
            </p>
          )}
        </div>
        <RunStatusBadge status={node.status} />
        <span className="font-mono text-xs tabular-nums text-text-muted w-16 text-right">
          {formatDuration(node.duration_ms || undefined)}
        </span>
        {node.input_track_count != null && node.output_track_count != null && (
          <span className="font-mono text-xs tabular-nums text-text-muted">
            {node.input_track_count} &rarr; {node.output_track_count}
          </span>
        )}
        {hasDetails && (
          <span className="text-text-faint">
            {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          </span>
        )}
      </div>
      {expanded && <PlaylistChangesPanel node={node} />}
    </div>
  );
}

/** Output tracks table showing the final playlist result with dynamic metric columns. */
function OutputTracksTable({
  tracks,
  metricColumns,
}: {
  tracks: Record<string, unknown>[];
  metricColumns: string[];
}) {
  if (tracks.length === 0) return null;

  return (
    <section className="mt-10 space-y-3">
      <SectionHeader title="Output Tracks" />
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-12 text-right">#</TableHead>
            <TableHead>Title</TableHead>
            <TableHead>Artist</TableHead>
            {metricColumns.map((col) => (
              <TableHead key={col} className="text-right">
                {formatMetricHeader(col)}
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {tracks.map((track, i) => (
            <TableRow key={String(track.track_id ?? i)}>
              <TableCell className="text-right font-mono text-xs tabular-nums text-text-faint">
                {(track.rank as number) ?? i + 1}
              </TableCell>
              <TableCell className="font-medium text-text">
                {String(track.title ?? "")}
              </TableCell>
              <TableCell className="text-text-muted">
                {String(track.artists ?? "")}
              </TableCell>
              {metricColumns.map((col) => (
                <TableCell
                  key={col}
                  className="text-right font-mono text-xs tabular-nums text-text-muted"
                >
                  {formatMetricValue(
                    (track.metrics as Record<string, unknown>)?.[col],
                  )}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </section>
  );
}

// --- Main page component ---

export function WorkflowRunDetail() {
  const { id, runId } = useParams<{ id: string; runId: string }>();
  const workflowId = Number(id);
  const runIdNum = Number(runId);

  const { data, isLoading, isError } =
    useGetWorkflowRunApiV1WorkflowsWorkflowIdRunsRunIdGet(workflowId, runIdNum);

  const { data: workflowData } =
    useGetWorkflowApiV1WorkflowsWorkflowIdGet(workflowId);

  const { isExecuting, execute } = useWorkflowExecution(workflowId);

  if (isLoading) return <RunDetailSkeleton />;

  if (isError) {
    return (
      <EmptyState
        icon={<HelpCircle className="size-10" />}
        heading="Run not found"
        description="This run doesn't exist or has been deleted."
      />
    );
  }

  const run = data?.status === 200 ? data.data : undefined;
  if (!run) return null;

  const workflow = workflowData?.status === 200 ? workflowData.data : undefined;
  const workflowName = workflow?.name ?? "Workflow";
  const currentDefVersion = workflow?.definition_version ?? 1;

  const tasks = run.definition_snapshot.tasks ?? [];
  const nodes = run.nodes ?? [];
  const outputTracks = (run.output_tracks ?? []) as Record<string, unknown>[];
  const statusConf = getStatusConfig(run.status);

  const versionMismatch =
    run.definition_version != null &&
    run.definition_version < currentDefVersion;

  // Build nodeStatuses map from persisted node records
  const nodeStatuses = new Map<string, NodeStatus>();
  for (const node of nodes) {
    nodeStatuses.set(node.node_id, {
      nodeId: node.node_id,
      nodeType: node.node_type,
      status: node.status as NodeStatus["status"],
      executionOrder: node.execution_order ?? 0,
      totalNodes: nodes.length,
      durationMs: node.duration_ms,
      inputTrackCount: node.input_track_count ?? undefined,
      outputTrackCount: node.output_track_count ?? undefined,
      errorMessage: node.error_message ?? undefined,
    });
  }

  const sortedNodes = [...nodes].sort(
    (a, b) => (a.execution_order ?? 0) - (b.execution_order ?? 0),
  );

  return (
    <div>
      <title>{`Run #${run.id} — Narada`}</title>
      <BackLink to={`/workflows/${workflowId}`}>{workflowName}</BackLink>

      <PageHeader
        title={`Run #${run.id}`}
        description={`${workflowName} — definition v${run.definition_version ?? 1}`}
        action={
          <div className="flex items-center gap-3">
            <span
              className={cn(
                "inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-display font-medium",
                statusConf.className,
              )}
            >
              {statusConf.icon}
              {statusConf.label}
            </span>
            <Button
              size="sm"
              disabled={isExecuting}
              onClick={execute}
              className="gap-1.5"
            >
              <Play size={14} className={isExecuting ? "animate-spin" : ""} />
              {isExecuting ? "Running..." : "Run Again"}
            </Button>
          </div>
        }
      />

      {/* Version mismatch warning */}
      {versionMismatch && (
        <div className="mb-6 flex items-center gap-2 rounded-lg border-l-2 border-primary bg-primary/5 px-4 py-3">
          <AlertTriangle size={14} className="shrink-0 text-primary" />
          <p className="font-display text-sm text-primary">
            Workflow definition has changed since this run (v
            {run.definition_version} → v{currentDefVersion}). Results may differ
            if re-run.
          </p>
        </div>
      )}

      {/* Run metadata */}
      <div className="mb-6 flex flex-wrap items-center gap-x-6 gap-y-1 text-sm text-text-muted">
        {run.started_at && (
          <span>
            Started{" "}
            <span className="font-mono text-text">
              {formatDate(run.started_at)}
            </span>
          </span>
        )}
        {run.duration_ms != null && (
          <span>
            Duration{" "}
            <span className="font-mono text-text">
              {formatDuration(run.duration_ms)}
            </span>
          </span>
        )}
        {run.output_track_count != null && (
          <span>
            Output{" "}
            <span className="font-mono text-text">
              {run.output_track_count} tracks
            </span>
          </span>
        )}
      </div>

      {run.error_message && (
        <div className="mb-6 rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-3">
          <p className="text-sm text-destructive">{run.error_message}</p>
        </div>
      )}

      {/* DAG from definition snapshot with execution overlay */}
      {tasks.length > 0 ? (
        <div className="h-[clamp(400px,60vh,900px)] rounded-lg border border-border-muted bg-surface-sunken">
          <WorkflowGraph
            tasks={tasks}
            nodeStatuses={nodeStatuses.size > 0 ? nodeStatuses : undefined}
          />
        </div>
      ) : (
        <EmptyState
          heading="No tasks in snapshot"
          description="The workflow definition snapshot has no tasks."
        />
      )}

      {/* Per-node execution details (expandable) */}
      {sortedNodes.length > 0 && (
        <section className="mt-10 space-y-3">
          <SectionHeader title="Node Execution Details" />
          <div className="space-y-2">
            {sortedNodes.map((node) => (
              <NodeExecutionRow key={node.node_id} node={node} />
            ))}
          </div>
        </section>
      )}

      {/* Output tracks table */}
      <OutputTracksTable
        tracks={outputTracks}
        metricColumns={run.metric_columns ?? []}
      />
    </div>
  );
}
