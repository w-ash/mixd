import { useQueryClient } from "@tanstack/react-query";
import { CalendarClock, Copy, Pencil, Play } from "lucide-react";
import { memo } from "react";
import { Link, useNavigate } from "react-router";
import type { WorkflowSummarySchema } from "#/api/generated/model";
import {
  getListWorkflowsApiV1WorkflowsGetQueryKey,
  useDuplicateWorkflowApiV1WorkflowsWorkflowIdDuplicatePost,
} from "#/api/generated/workflows/workflows";
import { getStatusConfig } from "#/components/shared/RunStatusBadge";
import { TableCard } from "#/components/shared/TableCard";
import { TitleLink } from "#/components/shared/TitleLink";
import { Button } from "#/components/ui/button";
import { TableCell, TableRow } from "#/components/ui/table";
import { useWorkflowExecution } from "#/hooks/useWorkflowExecution";
import { formatDate } from "#/lib/format";
import { toasts } from "#/lib/toasts";
import { cn } from "#/lib/utils";

/** Inline run button for a single workflow row. */
function WorkflowRunButton({
  workflowId,
  disabled,
}: {
  workflowId: string;
  disabled: boolean;
}) {
  const { isExecuting, execute } = useWorkflowExecution(workflowId);

  return (
    <Button
      size="icon"
      variant="ghost"
      className="size-7"
      disabled={disabled || isExecuting}
      onClick={execute}
      title="Run workflow"
    >
      <Play
        className={cn(
          "size-3.5",
          isExecuting && "animate-spin",
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
  runningWorkflowId,
}: {
  wf: WorkflowSummarySchema;
  runningWorkflowId: string | null;
}) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();

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
        disabled={runningWorkflowId !== null && runningWorkflowId !== wf.id}
      />
    </div>
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
  runningWorkflowId,
  variant,
  nextRun = null,
}: {
  wf: WorkflowSummarySchema;
  runningWorkflowId: string | null;
  variant: "card" | "table";
  /** Pre-formatted next-run label for an enabled schedule, or null if none. */
  nextRun?: string | null;
}) {
  const lastRun = wf.last_run;
  const runConf = lastRun ? getStatusConfig(lastRun.status) : null;
  const actions = (
    <WorkflowRowActions wf={wf} runningWorkflowId={runningWorkflowId} />
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
        {nextRun ? (
          <span className="inline-flex items-center gap-1 font-display text-primary">
            <CalendarClock className="size-3" />
            {nextRun}
          </span>
        ) : (
          <span className="text-text-faint">&mdash;</span>
        )}
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
