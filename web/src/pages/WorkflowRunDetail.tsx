import { AlertTriangle, HelpCircle, Play } from "lucide-react";
import { useParams } from "react-router";

import {
  useGetWorkflowApiV1WorkflowsWorkflowIdGet,
  useGetWorkflowRunApiV1WorkflowsWorkflowIdRunsRunIdGet,
} from "#/api/generated/workflows/workflows";
import { PageHeader } from "#/components/layout/PageHeader";
import { BackLink } from "#/components/shared/BackLink";
import { EmptyState } from "#/components/shared/EmptyState";
import { getStatusConfig } from "#/components/shared/RunStatusBadge";
import { SectionHeader } from "#/components/shared/SectionHeader";
import { DetailHeaderSkeleton } from "#/components/shared/skeletons";
import { WorkflowGraph } from "#/components/shared/WorkflowGraph";
import { Button } from "#/components/ui/button";
import { Skeleton } from "#/components/ui/skeleton";
import { NodeExecutionRow } from "#/components/workflow/run-detail/NodeExecutionRow";
import { OutputTracksTable } from "#/components/workflow/run-detail/OutputTracksTable";
import { useWorkflowExecution } from "#/hooks/useWorkflowExecution";
import { formatDate, formatDuration } from "#/lib/format";
import type { NodeStatus } from "#/lib/sse-types";
import { cn } from "#/lib/utils";

// --- Sub-components ---

function RunDetailSkeleton() {
  return (
    <div className="space-y-6">
      <DetailHeaderSkeleton />
      <Skeleton className="h-[700px] w-full rounded-lg" />
    </div>
  );
}

// --- Main page component ---

export function WorkflowRunDetail() {
  const { id, runId } = useParams<{ id: string; runId: string }>();
  const workflowId = id ?? "";
  const runIdStr = runId ?? "";

  const { data, isLoading, isError } =
    useGetWorkflowRunApiV1WorkflowsWorkflowIdRunsRunIdGet(workflowId, runIdStr);

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
      <title>{`Run #${run.run_number} — Mixd`}</title>
      <BackLink to={`/workflows/${workflowId}`}>{workflowName}</BackLink>

      <PageHeader
        title={`Run #${run.run_number}`}
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
              <Play className={cn("size-3.5", isExecuting && "animate-spin")} />
              {isExecuting ? "Running..." : "Run Again"}
            </Button>
          </div>
        }
      />

      {/* Version mismatch warning */}
      {versionMismatch && (
        <div className="mb-6 flex items-center gap-2 rounded-lg border-l-2 border-primary bg-primary/5 px-4 py-3">
          <AlertTriangle className="size-3.5 shrink-0 text-primary" />
          <p className="font-display text-sm text-primary">
            Workflow definition has changed since this run (v
            {run.definition_version} → v{currentDefVersion}). Results may differ
            if re-run.
          </p>
        </div>
      )}

      {/* Run metadata */}
      <div className="mb-6 flex flex-wrap items-center gap-x-6 gap-y-2 text-sm text-text-muted">
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
        <section className="mt-8 space-y-3">
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
