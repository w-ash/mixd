import { Copy, GitBranch, Lock, Pencil, Play, Plus } from "lucide-react";
import { memo } from "react";
import { Link } from "react-router";

import type { WorkflowSummarySchema } from "#/api/generated/model";
import { useListWorkflowsApiV1WorkflowsGet } from "#/api/generated/workflows/workflows";
import { STALE } from "#/api/query-client";
import { PageHeader } from "#/components/layout/PageHeader";
import { EmptyState } from "#/components/shared/EmptyState";
import { QueryErrorState } from "#/components/shared/QueryErrorState";
import { ResponsiveTable } from "#/components/shared/ResponsiveTable";
import { getStatusConfig } from "#/components/shared/RunStatusBadge";
import { TableCard } from "#/components/shared/TableCard";
import { TablePagination } from "#/components/shared/TablePagination";
import { TitleLink } from "#/components/shared/TitleLink";
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
import { useWorkflowExecutionContext } from "#/contexts/WorkflowExecutionContext";
import { usePagination } from "#/hooks/usePagination";
import { useWorkflowExecution } from "#/hooks/useWorkflowExecution";
import { formatDate } from "#/lib/format";
import { cn } from "#/lib/utils";

function WorkflowTableSkeleton() {
  return (
    <div className="space-y-3">
      {Array.from({ length: 6 }).map((_, i) => (
        // biome-ignore lint/suspicious/noArrayIndexKey: static skeleton placeholders
        <div key={i} className="flex items-center gap-4">
          <Skeleton className="h-5 w-52" />
          <Skeleton className="h-5 w-10" />
          <Skeleton className="h-5 w-20" />
          <Skeleton className="h-5 w-32" />
        </div>
      ))}
    </div>
  );
}

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

/** Edit/use-template + run buttons. Reused in the table row and the card. */
function WorkflowRowActions({
  wf,
  runningWorkflowId,
}: {
  wf: WorkflowSummarySchema;
  runningWorkflowId: string | null;
}) {
  return (
    <div className="flex items-center gap-1">
      {wf.is_template ? (
        <Button
          size="icon"
          variant="ghost"
          className="size-7"
          title="Use template"
          asChild
        >
          <Link to={`/workflows/new?from=${wf.id}`}>
            <Copy className="size-3.5 text-text-muted" />
          </Link>
        </Button>
      ) : (
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
      )}
      <WorkflowRunButton
        workflowId={wf.id}
        disabled={runningWorkflowId !== null && runningWorkflowId !== wf.id}
      />
    </div>
  );
}

/**
 * Card representation of a workflow row — used by ResponsiveTable below the
 * @2xl container threshold (typically iPhone / iPad portrait widths).
 */
function WorkflowCard({
  wf,
  runningWorkflowId,
}: {
  wf: WorkflowSummarySchema;
  runningWorkflowId: string | null;
}) {
  const lastRun = wf.last_run;
  const runConf = lastRun ? getStatusConfig(lastRun.status) : null;

  return (
    <TableCard
      trailing={
        <WorkflowRowActions wf={wf} runningWorkflowId={runningWorkflowId} />
      }
    >
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <TitleLink to={`/workflows/${wf.id}`} viewTransition>
          {wf.name}
        </TitleLink>
        {wf.is_template && (
          <Badge variant="outline" className="gap-1 text-[10px]">
            <Lock className="size-2.5" aria-hidden="true" />
            Template
          </Badge>
        )}
      </div>
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
        <span>Updated {formatDate(wf.updated_at)}</span>
      </div>
    </TableCard>
  );
}

const WorkflowRow = memo(function WorkflowRow({
  wf,
  runningWorkflowId,
}: {
  wf: WorkflowSummarySchema;
  runningWorkflowId: string | null;
}) {
  const lastRun = wf.last_run;
  const runConf = lastRun ? getStatusConfig(lastRun.status) : null;

  return (
    <TableRow>
      <TableCell>
        <div className="flex items-center gap-2">
          <Link
            to={`/workflows/${wf.id}`}
            viewTransition
            className="font-medium text-text hover:text-primary transition-colors"
          >
            {wf.name}
          </Link>
          {wf.is_template && (
            <Badge variant="outline" className="gap-1 text-[10px]">
              <Lock className="size-2.5" aria-hidden="true" />
              Template
            </Badge>
          )}
        </div>
        {wf.description && (
          <p className="mt-0.5 text-xs text-text-muted line-clamp-1">
            {wf.description}
          </p>
        )}
      </TableCell>
      <TableCell className="text-right tabular-nums">{wf.task_count}</TableCell>
      <TableCell>
        {runConf ? (
          <span
            className={cn(
              "inline-flex items-center gap-1 text-xs font-display",
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
      <TableCell className="text-right text-text-muted text-sm">
        {formatDate(wf.updated_at)}
      </TableCell>
      <TableCell className="text-right">
        <div className="flex justify-end">
          <WorkflowRowActions wf={wf} runningWorkflowId={runningWorkflowId} />
        </div>
      </TableCell>
    </TableRow>
  );
});

export function Workflows() {
  const { page, limit, offset, setPage } = usePagination(0);
  const ctx = useWorkflowExecutionContext();
  const runningWorkflowId = ctx.isExecuting ? ctx.workflowId : null;

  const { data, isLoading, isError, error } = useListWorkflowsApiV1WorkflowsGet(
    { limit, offset },
    { query: { staleTime: STALE.SLOW, placeholderData: (prev) => prev } },
  );

  const response = data?.status === 200 ? data.data : undefined;
  const workflows = response?.data ?? [];
  const total = response?.total ?? 0;
  const totalPages = total > 0 ? Math.ceil(total / limit) : 1;

  return (
    <div>
      <title>Workflows — Mixd</title>
      <PageHeader
        title="Workflows"
        description="Declarative pipelines that compose your music criteria into playlists."
        action={
          <Button size="sm" asChild className="gap-1.5">
            <Link to="/workflows/new">
              <Plus className="size-3.5" />
              New Workflow
            </Link>
          </Button>
        }
      />

      {isLoading && <WorkflowTableSkeleton />}

      {isError && (
        <QueryErrorState error={error} heading="Failed to load workflows" />
      )}

      {!isLoading && !isError && workflows.length === 0 && (
        <EmptyState
          icon={<GitBranch className="size-10" />}
          heading="No workflows yet"
          description="Workflows define how your music is filtered, sorted, and assembled into playlists."
          action={
            <Button size="sm" asChild className="gap-1.5">
              <Link to="/workflows/new">
                <Plus className="size-3.5" />
                New Workflow
              </Link>
            </Button>
          }
        />
      )}

      {!isLoading && !isError && workflows.length > 0 && (
        <>
          <ResponsiveTable
            cards={
              <div className="flex flex-col gap-2">
                {workflows.map((wf) => (
                  <WorkflowCard
                    key={wf.id}
                    wf={wf}
                    runningWorkflowId={runningWorkflowId}
                  />
                ))}
              </div>
            }
            table={
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Name</TableHead>
                    <TableHead className="w-20 text-right">Tasks</TableHead>
                    <TableHead className="w-28">Last Run</TableHead>
                    <TableHead className="w-36 text-right">Updated</TableHead>
                    <TableHead className="w-12" />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {workflows.map((wf) => (
                    <WorkflowRow
                      key={wf.id}
                      wf={wf}
                      runningWorkflowId={runningWorkflowId}
                    />
                  ))}
                </TableBody>
              </Table>
            }
          />

          <TablePagination
            page={Math.min(page, totalPages)}
            totalPages={totalPages}
            total={total}
            limit={limit}
            onPageChange={setPage}
          />
        </>
      )}
    </div>
  );
}
