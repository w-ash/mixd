import { useQueryClient } from "@tanstack/react-query";
import { Copy, HelpCircle, Pencil, Play } from "lucide-react";
import { useEffect } from "react";
import { Link, useNavigate, useParams } from "react-router";
import {
  getListWorkflowsApiV1WorkflowsGetQueryKey,
  useDuplicateWorkflowApiV1WorkflowsWorkflowIdDuplicatePost,
  useGetWorkflowApiV1WorkflowsWorkflowIdGet,
  useGetWorkflowScheduleApiV1WorkflowsWorkflowIdScheduleGet,
  useListWorkflowRunsApiV1WorkflowsWorkflowIdRunsGet,
} from "#/api/generated/workflows/workflows";
import { STALE } from "#/api/query-client";
import { PageHeader } from "#/components/layout/PageHeader";
import { BackLink } from "#/components/shared/BackLink";
import { EmptyState } from "#/components/shared/EmptyState";
import { Button } from "#/components/ui/button";
import { Skeleton } from "#/components/ui/skeleton";
import { RunHistoryTable } from "#/components/workflow/RunHistoryTable";
import { WorkflowScheduleCard } from "#/components/workflow/WorkflowScheduleCard";
import { WorkflowStatusPanel } from "#/components/workflow/WorkflowStatusPanel";
import { useWorkflowExecutionContext } from "#/contexts/WorkflowExecutionContext";
import { useActiveRun } from "#/hooks/useActiveRuns";
import { useWorkflowExecution } from "#/hooks/useWorkflowExecution";
import { formatNextRun } from "#/lib/schedule";
import { toasts } from "#/lib/toasts";
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

export function WorkflowDetail() {
  const { id } = useParams<{ id: string }>();
  const workflowId = id ?? "";
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const { data, isLoading, isError } =
    useGetWorkflowApiV1WorkflowsWorkflowIdGet(workflowId, {
      query: { staleTime: STALE.SLOW },
    });

  const duplicate = useDuplicateWorkflowApiV1WorkflowsWorkflowIdDuplicatePost({
    mutation: {
      onSuccess: (res) => {
        if (res.status === 201) {
          queryClient.invalidateQueries({
            queryKey: getListWorkflowsApiV1WorkflowsGetQueryKey(),
          });
          toasts.success("Workflow duplicated");
          navigate(`/workflows/${res.data.id}/edit`);
        }
      },
      meta: { errorLabel: "Failed to duplicate workflow" },
    },
  });

  const { data: runsData } = useListWorkflowRunsApiV1WorkflowsWorkflowIdRunsGet(
    workflowId,
    { limit: 10, offset: 0 },
    { query: { staleTime: STALE.SLOW } },
  );

  // Schedule powers the idle cadence line. Same query key as the schedule card
  // below, so this is deduped, not a second network call.
  const { data: scheduleData } =
    useGetWorkflowScheduleApiV1WorkflowsWorkflowIdScheduleGet(workflowId, {
      query: { staleTime: STALE.SLOW, retry: false },
    });

  const {
    isExecuting,
    nodeStatuses,
    runAccepted,
    subProgress,
    runId,
    execute,
  } = useWorkflowExecution(workflowId);

  // App-global active-runs source: the in-flight run for THIS workflow (server
  // truth), or null. Drives reconnection + the panel's active state.
  const { data: activeRun = null } = useActiveRun(workflowId);

  const {
    adoptRun,
    operationId,
    workflowId: drivingWorkflowId,
  } = useWorkflowExecutionContext();

  // Reconnect on mount / when an active run appears: adopt the in-flight run so
  // a reloaded page shows real current state. Skip if already driving this run,
  // or if the single execution slot is busy with another workflow (that page
  // keeps its own live stream; this one would otherwise steal the connection).
  useEffect(() => {
    const opId = activeRun?.operation_id;
    if (!opId || !activeRun) return;
    if (operationId === opId) return;
    if (operationId !== null && drivingWorkflowId !== workflowId) return;
    adoptRun(workflowId, opId, activeRun.id);
  }, [activeRun, operationId, drivingWorkflowId, workflowId, adoptRun]);

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

  const schedule = scheduleData?.status === 200 ? scheduleData.data : null;
  const nextRunLabel =
    schedule && schedule.status === "enabled" && schedule.next_run_at
      ? `Next run ${formatNextRun(schedule)}`
      : null;

  const runDisabled = isExecuting || activeRun !== null;

  return (
    <div>
      <title>{workflow.name} — Mixd</title>
      <BackLink to="/workflows">Workflows</BackLink>

      <PageHeader
        title={workflow.name}
        description={workflow.description ?? undefined}
        action={
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              className="gap-1.5"
              disabled={duplicate.isPending}
              onClick={() => duplicate.mutate({ workflowId })}
            >
              <Copy className="size-3.5" />
              Duplicate
            </Button>
            <Button variant="outline" size="sm" asChild className="gap-1.5">
              <Link to={`/workflows/${workflowId}/edit`}>
                <Pencil className="size-3.5" />
                Edit
              </Link>
            </Button>
            <Button
              size="sm"
              disabled={runDisabled}
              onClick={execute}
              className="gap-1.5"
            >
              <Play className={cn("size-3.5", runDisabled && "animate-spin")} />
              {runDisabled ? "Running..." : "Run"}
            </Button>
          </div>
        }
      />

      <WorkflowStatusPanel
        workflowId={workflowId}
        tasks={tasks}
        lastRun={lastRun}
        currentDefinitionVersion={workflow.definition_version ?? 1}
        activeRun={activeRun}
        nodeStatuses={nodeStatuses}
        isExecuting={isExecuting}
        runAccepted={runAccepted}
        subProgress={subProgress}
        runId={runId}
        nextRunLabel={nextRunLabel}
      />

      <RunHistoryTable runs={runs} workflowId={workflowId} />

      <WorkflowScheduleCard workflowId={workflowId} />
    </div>
  );
}
