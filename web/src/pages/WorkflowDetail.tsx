import { HelpCircle, Lock, Pencil, Play } from "lucide-react";
import { Link, useParams } from "react-router";
import type { WorkflowRunSummarySchema } from "#/api/generated/model";
import {
  useGetWorkflowApiV1WorkflowsWorkflowIdGet,
  useListWorkflowRunsApiV1WorkflowsWorkflowIdRunsGet,
} from "#/api/generated/workflows/workflows";
import { STALE } from "#/api/query-client";
import { PageHeader } from "#/components/layout/PageHeader";
import { BackLink } from "#/components/shared/BackLink";
import { EmptyState } from "#/components/shared/EmptyState";
import { LastRunCard } from "#/components/shared/LastRunCard";
import { PipelineStrip } from "#/components/shared/PipelineStrip";
import { RunStatusBadge } from "#/components/shared/RunStatusBadge";
import { SectionHeader } from "#/components/shared/SectionHeader";
import { Badge } from "#/components/ui/badge";
import { Button } from "#/components/ui/button";
import { Skeleton } from "#/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "#/components/ui/table";
import { useWorkflowExecution } from "#/hooks/useWorkflowExecution";
import { formatDate, formatDuration } from "#/lib/format";
import { cn } from "#/lib/utils";

function DetailSkeleton() {
  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-4 w-96" />
      </div>
      <Skeleton className="h-16 w-full rounded-lg" />
      <Skeleton className="h-24 w-full rounded-lg" />
    </div>
  );
}

function RunHistoryTable({
  runs,
  workflowId,
}: {
  runs: WorkflowRunSummarySchema[];
  workflowId: string;
}) {
  if (runs.length === 0) return null;

  return (
    <section className="mt-8 space-y-3">
      <SectionHeader title="Execution History" />
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-16">Run</TableHead>
            <TableHead>Status</TableHead>
            <TableHead className="text-right">Duration</TableHead>
            <TableHead className="text-right">Tracks</TableHead>
            <TableHead className="text-right">Started</TableHead>
            <TableHead className="w-16" />
          </TableRow>
        </TableHeader>
        <TableBody>
          {runs.map((run) => (
            <TableRow key={run.id}>
              <TableCell className="font-mono text-xs text-text-muted">
                #{run.id}
              </TableCell>
              <TableCell>
                <RunStatusBadge status={run.status} />
              </TableCell>
              <TableCell className="text-right font-mono text-xs tabular-nums text-text-muted">
                {formatDuration(run.duration_ms)}
              </TableCell>
              <TableCell className="text-right font-mono text-xs tabular-nums text-text-muted">
                {run.output_track_count ?? "\u2014"}
              </TableCell>
              <TableCell className="text-right text-xs text-text-muted">
                {formatDate(run.started_at ?? run.created_at)}
              </TableCell>
              <TableCell className="text-right">
                <Link
                  to={`/workflows/${workflowId}/runs/${run.id}`}
                  className="text-xs text-text-muted hover:text-text transition-colors"
                >
                  View
                </Link>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </section>
  );
}

export function WorkflowDetail() {
  const { id } = useParams<{ id: string }>();
  const workflowId = id ?? "";

  const { data, isLoading, isError } =
    useGetWorkflowApiV1WorkflowsWorkflowIdGet(workflowId, {
      query: { staleTime: STALE.SLOW },
    });

  const { data: runsData } = useListWorkflowRunsApiV1WorkflowsWorkflowIdRunsGet(
    workflowId,
    {
      limit: 10,
      offset: 0,
    },
    { query: { staleTime: STALE.SLOW } },
  );

  const { isExecuting, nodeStatuses, execute } =
    useWorkflowExecution(workflowId);

  if (isLoading) return <DetailSkeleton />;

  if (isError) {
    return (
      <EmptyState
        icon={<HelpCircle className="size-10" />}
        heading="Workflow not found"
        description="This workflow doesn't exist or has been deleted."
      />
    );
  }

  const workflow = data?.status === 200 ? data.data : undefined;
  if (!workflow) return null;

  const tasks = workflow.definition.tasks ?? [];
  const runs = runsData?.status === 200 ? (runsData.data.data ?? []) : [];
  const lastRun = workflow.last_run ?? null;

  return (
    <div>
      <title>{workflow.name} — Mixd</title>
      <BackLink to="/workflows">Workflows</BackLink>

      <PageHeader
        title={workflow.name}
        description={workflow.description ?? undefined}
        action={
          <div className="flex items-center gap-2">
            {workflow.is_template && (
              <Badge variant="outline" className="gap-1">
                <Lock className="size-3" aria-hidden="true" />
                Template
              </Badge>
            )}
            {!workflow.is_template && (
              <Button variant="outline" size="sm" asChild className="gap-1.5">
                <Link to={`/workflows/${workflowId}/edit`}>
                  <Pencil className="size-3.5" />
                  Edit
                </Link>
              </Button>
            )}
            <Button
              size="sm"
              disabled={isExecuting}
              onClick={execute}
              className="gap-1.5"
            >
              <Play className={cn("size-3.5", isExecuting && "animate-spin")} />
              {isExecuting ? "Running..." : "Run"}
            </Button>
          </div>
        }
      />

      {/* Compact pipeline strip replaces full-height DAG */}
      {tasks.length > 0 && (
        <PipelineStrip
          tasks={tasks}
          nodeStatuses={nodeStatuses}
          isExecuting={isExecuting}
          className="mb-6"
        />
      )}

      {/* Last run card */}
      <LastRunCard
        run={lastRun}
        currentDefinitionVersion={workflow.definition_version ?? 1}
        workflowId={workflowId}
      />

      <RunHistoryTable runs={runs} workflowId={workflowId} />
    </div>
  );
}
