import { ArrowLeft, HelpCircle } from "lucide-react";
import { Link, useParams } from "react-router";

import { useGetWorkflowRunApiV1WorkflowsWorkflowIdRunsRunIdGet } from "@/api/generated/workflows/workflows";
import { PageHeader } from "@/components/layout/PageHeader";
import { EmptyState } from "@/components/shared/EmptyState";
import {
  getStatusConfig,
  RunStatusBadge,
} from "@/components/shared/RunStatusBadge";
import { WorkflowGraph } from "@/components/shared/WorkflowGraph";
import { Skeleton } from "@/components/ui/skeleton";
import type { NodeStatus } from "@/hooks/useWorkflowExecution";
import { formatDate, formatDuration } from "@/lib/format";
import { cn } from "@/lib/utils";

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

export function WorkflowRunDetail() {
  const { id, runId } = useParams<{ id: string; runId: string }>();
  const workflowId = Number(id);
  const runIdNum = Number(runId);

  const { data, isLoading, isError } =
    useGetWorkflowRunApiV1WorkflowsWorkflowIdRunsRunIdGet(workflowId, runIdNum);

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

  const tasks = run.definition_snapshot.tasks ?? [];
  const nodes = run.nodes ?? [];
  const statusConf = getStatusConfig(run.status);

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

  return (
    <div>
      <title>{`Run #${run.id} — Narada`}</title>
      <Link
        to={`/workflows/${workflowId}`}
        className="mb-4 inline-flex items-center gap-1.5 text-sm text-text-muted hover:text-text transition-colors"
      >
        <ArrowLeft size={14} />
        Back to workflow
      </Link>

      <PageHeader
        title={`Run #${run.id}`}
        description={`From workflow definition snapshot`}
        action={
          <span
            className={cn(
              "inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-display font-medium",
              statusConf.className,
            )}
          >
            {statusConf.icon}
            {statusConf.label}
          </span>
        }
      />

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

      {/* Per-node details table */}
      {nodes.length > 0 && (
        <section className="mt-10">
          <h2 className="mb-3 font-display text-base font-semibold text-text">
            Node Execution Details
          </h2>
          <div className="space-y-2">
            {[...nodes]
              .sort(
                (a, b) => (a.execution_order ?? 0) - (b.execution_order ?? 0),
              )
              .map((node) => (
                <div
                  key={node.node_id}
                  className="flex items-center gap-4 rounded-lg border border-border bg-surface-elevated px-4 py-3"
                >
                  <span className="font-mono text-xs text-text-faint w-6">
                    {node.execution_order}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="font-display text-sm font-medium text-text">
                        {node.node_id}
                      </span>
                      <span className="font-mono text-[10px] text-text-faint">
                        {node.node_type}
                      </span>
                    </div>
                    {node.error_message && (
                      <p className="mt-0.5 text-xs text-destructive truncate">
                        {node.error_message}
                      </p>
                    )}
                  </div>
                  <RunStatusBadge status={node.status} />
                  <span className="font-mono text-xs text-text-muted w-16 text-right">
                    {formatDuration(node.duration_ms || undefined)}
                  </span>
                  {node.input_track_count != null &&
                    node.output_track_count != null && (
                      <span className="font-mono text-xs text-text-muted">
                        {node.input_track_count} &rarr;{" "}
                        {node.output_track_count}
                      </span>
                    )}
                </div>
              ))}
          </div>
        </section>
      )}
    </div>
  );
}
