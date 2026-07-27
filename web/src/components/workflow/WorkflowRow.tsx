import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CalendarClock, Copy, Pencil, Play } from "lucide-react";
import { memo } from "react";
import { Link, useNavigate } from "react-router";
import type { WorkflowSummarySchema } from "#/api/generated/model";
import { useDuplicateWorkflowApiV1WorkflowsWorkflowIdDuplicatePost } from "#/api/generated/workflows/workflows";
import { getStatusConfig } from "#/components/shared/RunStatusBadge";
import { TableCard } from "#/components/shared/TableCard";
import { TitleLink } from "#/components/shared/TitleLink";
import { Button } from "#/components/ui/button";
import { TableCell, TableRow } from "#/components/ui/table";
import { useWorkflowExecution } from "#/hooks/useWorkflowExecution";
import { formatDate } from "#/lib/format";
import { toasts } from "#/lib/toasts";
import { cn } from "#/lib/utils";
import { afterWorkflowSaved } from "#/lib/workflow-queries";

/** Inline run button for a single workflow row. */
function WorkflowRunButton({
  workflowId,
  disabled,
  isRunning,
}: {
  workflowId: string;
  disabled: boolean;
  /** Server-side truth: this workflow has a run in flight, wherever it began. */
  isRunning: boolean;
}) {
  const { isExecuting, execute } = useWorkflowExecution(workflowId);
  const spinning = isExecuting || isRunning;

  return (
    <Button
      size="icon"
      variant="ghost"
      className="size-7"
      disabled={disabled || spinning}
      onClick={execute}
      title={spinning ? "Workflow is running" : "Run workflow"}
    >
      <Play
        className={cn(
          "size-3.5",
          spinning && "animate-spin",
          "text-text-muted",
        )}
      />
    </Button>
  );
}

/**
 * Edit + Duplicate + Run buttons. Every workflow is user-owned and editable,
 * so there is no template/custom fork — the same actions apply to every row.
 * Reused by both the desktop table row and the mobile card.
 */
export function WorkflowRowActions({
  wf,
  runningWorkflowIds,
  localRunWorkflowId,
}: {
  wf: WorkflowSummarySchema;
  /** Workflows with a server-side run in flight (any origin). */
  runningWorkflowIds: ReadonlySet<string>;
  /** The workflow this tab is streaming, if any. */
  localRunWorkflowId: string | null;
}) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  // Two independent reasons a Run button is unavailable, deliberately kept
  // apart. The backend's uq_workflow_runs_active index is per-workflow, so a
  // run elsewhere only blocks THAT workflow — but this tab has a single SSE
  // slot, so a local run blocks every OTHER row.
  const isRunning = runningWorkflowIds.has(wf.id);
  const localSlotBusy =
    localRunWorkflowId !== null && localRunWorkflowId !== wf.id;

  const duplicate = useDuplicateWorkflowApiV1WorkflowsWorkflowIdDuplicatePost({
    mutation: {
      onSuccess: (res) => {
        if (res.status === 201) {
          afterWorkflowSaved(queryClient, res.data.id, res.data, res.headers);
          toasts.success("Workflow duplicated");
          navigate(`/workflows/${res.data.id}/edit`);
        }
      },
      meta: { errorLabel: "Failed to duplicate workflow" },
    },
  });

  return (
    <div className="flex items-center gap-1">
      <Button
        size="icon"
        variant="ghost"
        className="size-7"
        title="Edit workflow"
        asChild
      >
        <Link to={`/workflows/${wf.id}/edit`}>
          <Pencil className="size-3.5 text-text-muted" />
        </Link>
      </Button>
      <Button
        size="icon"
        variant="ghost"
        className="size-7"
        title="Duplicate workflow"
        disabled={duplicate.isPending}
        onClick={() => duplicate.mutate({ workflowId: wf.id })}
      >
        <Copy className="size-3.5 text-text-muted" />
      </Button>
      <WorkflowRunButton
        workflowId={wf.id}
        disabled={localSlotBusy}
        isRunning={isRunning}
      />
    </div>
  );
}

/** Marker shown when a workflow's schedule is in a failed streak. */
function FailingMarker() {
  return (
    <span
      className="inline-flex items-center gap-1 font-display text-status-expired"
      title="Recent scheduled runs failed"
    >
      <AlertTriangle className="size-3" />
      Failing
    </span>
  );
}

/**
 * Single workflow-row renderer used by both the mobile card list and the
 * desktop table (ResponsiveTable swaps between them at the @2xl threshold).
 * `variant` picks the wrapper; the title link, last-run summary, and
 * `<WorkflowRowActions>` are identical between the two.
 */
export const WorkflowRow = memo(function WorkflowRow({
  wf,
  runningWorkflowIds,
  localRunWorkflowId,
  variant,
  nextRun = null,
  scheduleFailing = false,
}: {
  wf: WorkflowSummarySchema;
  /** Workflows with a server-side run in flight (any origin). */
  runningWorkflowIds: ReadonlySet<string>;
  /** The workflow this tab is streaming, if any. */
  localRunWorkflowId: string | null;
  variant: "card" | "table";
  /** Pre-formatted next-run label for an enabled schedule, or null if none. */
  nextRun?: string | null;
  /** True when the workflow's schedule has a non-zero failure streak. */
  scheduleFailing?: boolean;
}) {
  // A live run outranks the last finished one — showing "Completed" while the
  // workflow is mid-run is the stale reading this change exists to remove.
  const isRunning = runningWorkflowIds.has(wf.id);
  const lastRun = wf.last_run;
  const runConf = isRunning
    ? getStatusConfig("running")
    : lastRun
      ? getStatusConfig(lastRun.status)
      : null;
  const runCount = wf.successful_run_count ?? 0;
  const actions = (
    <WorkflowRowActions
      wf={wf}
      runningWorkflowIds={runningWorkflowIds}
      localRunWorkflowId={localRunWorkflowId}
    />
  );

  if (variant === "card") {
    return (
      <TableCard trailing={actions}>
        <TitleLink to={`/workflows/${wf.id}`} viewTransition>
          {wf.name}
        </TitleLink>
        {wf.description && (
          <p className="mt-0.5 line-clamp-1 text-xs text-text-muted">
            {wf.description}
          </p>
        )}
        <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-text-muted">
          <span className="tabular-nums">
            {wf.task_count} task{wf.task_count === 1 ? "" : "s"}
          </span>
          {runCount > 0 && (
            <span className="font-mono tabular-nums">
              {runCount} run{runCount === 1 ? "" : "s"}
            </span>
          )}
          {runConf && (
            <span
              className={cn(
                "inline-flex items-center gap-1 font-display",
                runConf.className,
              )}
            >
              {runConf.icon}
              {runConf.label}
            </span>
          )}
          {nextRun && (
            <span className="inline-flex items-center gap-1 font-display text-primary">
              <CalendarClock className="size-3" />
              {nextRun}
            </span>
          )}
          {scheduleFailing && <FailingMarker />}
          <span>Updated {formatDate(wf.updated_at)}</span>
        </div>
      </TableCard>
    );
  }

  return (
    <TableRow>
      <TableCell>
        <Link
          to={`/workflows/${wf.id}`}
          viewTransition
          className="font-medium text-text transition-colors hover:text-primary"
        >
          {wf.name}
        </Link>
        {wf.description && (
          <p className="mt-0.5 line-clamp-1 text-xs text-text-muted">
            {wf.description}
          </p>
        )}
      </TableCell>
      <TableCell className="text-right tabular-nums">{wf.task_count}</TableCell>
      <TableCell
        className="text-right font-mono text-sm tabular-nums"
        title={`${runCount} successful run${runCount === 1 ? "" : "s"}`}
      >
        {runCount > 0 ? (
          runCount
        ) : (
          <span className="text-text-faint">&mdash;</span>
        )}
      </TableCell>
      <TableCell>
        {runConf ? (
          <span
            className={cn(
              "inline-flex items-center gap-1 font-display text-xs",
              runConf.className,
            )}
          >
            {runConf.icon}
            {runConf.label}
          </span>
        ) : (
          <span className="text-xs text-text-faint">&mdash;</span>
        )}
      </TableCell>
      <TableCell className="text-xs text-text-muted">
        <div className="flex flex-col gap-0.5">
          {nextRun ? (
            <span className="inline-flex items-center gap-1 font-display text-primary">
              <CalendarClock className="size-3" />
              {nextRun}
            </span>
          ) : (
            !scheduleFailing && <span className="text-text-faint">&mdash;</span>
          )}
          {scheduleFailing && <FailingMarker />}
        </div>
      </TableCell>
      <TableCell className="text-right text-sm text-text-muted">
        {formatDate(wf.updated_at)}
      </TableCell>
      <TableCell className="text-right">
        <div className="flex justify-end">{actions}</div>
      </TableCell>
    </TableRow>
  );
});
