import { GitBranch, LayoutTemplate, Plus } from "lucide-react";
import { useMemo } from "react";
import { Link } from "react-router";

import type { ScheduleResponse } from "#/api/generated/model";
import { useListSchedulesApiV1SchedulesGet } from "#/api/generated/schedules/schedules";
import { useListWorkflowsApiV1WorkflowsGet } from "#/api/generated/workflows/workflows";
import { STALE } from "#/api/query-client";
import { PageHeader } from "#/components/layout/PageHeader";
import { EmptyState } from "#/components/shared/EmptyState";
import { QueryErrorState } from "#/components/shared/QueryErrorState";
import { ResponsiveTable } from "#/components/shared/ResponsiveTable";
import { TablePagination } from "#/components/shared/TablePagination";
import { Button } from "#/components/ui/button";
import { Skeleton } from "#/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableHead,
  TableHeader,
  TableRow,
} from "#/components/ui/table";
import { TemplateGalleryDialog } from "#/components/workflow/TemplateGalleryDialog";
import { WorkflowRow } from "#/components/workflow/WorkflowRow";
import { useWorkflowExecutionContext } from "#/contexts/WorkflowExecutionContext";
import { usePagination } from "#/hooks/usePagination";
import { formatNextRun, isScheduleFailing } from "#/lib/schedule";

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

/** "New Workflow" + "From template" — shared by the header and the empty state. */
function NewWorkflowActions() {
  return (
    <div className="flex items-center gap-2">
      <TemplateGalleryDialog
        trigger={
          <Button size="sm" variant="outline" className="gap-1.5">
            <LayoutTemplate className="size-3.5" />
            From template
          </Button>
        }
      />
      <Button size="sm" asChild className="gap-1.5">
        <Link to="/workflows/new">
          <Plus className="size-3.5" />
          New Workflow
        </Link>
      </Button>
    </div>
  );
}

export function Workflows() {
  const { page, limit, offset, setPage } = usePagination(0);
  const ctx = useWorkflowExecutionContext();
  const runningWorkflowId = ctx.isExecuting ? ctx.workflowId : null;

  // Templates live in the gallery now, not the list — this is purely the
  // user's own, editable workflows.
  const { data, isLoading, isError, error } = useListWorkflowsApiV1WorkflowsGet(
    { limit, offset },
    { query: { staleTime: STALE.SLOW, placeholderData: (prev) => prev } },
  );

  const response = data?.status === 200 ? data.data : undefined;
  const workflows = response?.data ?? [];
  const total = response?.total ?? 0;
  const totalPages = total > 0 ? Math.ceil(total / limit) : 1;

  // One fetch powers both the "Next run" column and the failing-schedule marker
  // — no per-row N+1. Map each workflow to its whole schedule for O(1) lookup;
  // the row derives next-run and failing state from it.
  const { data: schedulesData } = useListSchedulesApiV1SchedulesGet({
    query: { staleTime: STALE.SLOW },
  });
  const scheduleByWorkflow = useMemo(() => {
    const map = new Map<string, ScheduleResponse>();
    const rows = schedulesData?.status === 200 ? schedulesData.data.data : [];
    for (const s of rows) {
      if (s.target_type === "workflow" && s.workflow_id) {
        map.set(s.workflow_id, s);
      }
    }
    return map;
  }, [schedulesData]);

  function rowSchedule(workflowId: string) {
    const s = scheduleByWorkflow.get(workflowId);
    return {
      nextRun:
        s && s.status === "enabled" && s.next_run_at ? formatNextRun(s) : null,
      scheduleFailing: s ? isScheduleFailing(s) : false,
    };
  }

  return (
    <div>
      <title>Workflows — Mixd</title>
      <PageHeader
        title="Workflows"
        description="Declarative pipelines that compose your music criteria into playlists."
        action={<NewWorkflowActions />}
      />

      {isLoading && <WorkflowTableSkeleton />}

      {isError && (
        <QueryErrorState error={error} heading="Failed to load workflows" />
      )}

      {!isLoading && !isError && workflows.length === 0 && (
        <EmptyState
          icon={<GitBranch className="size-10" />}
          heading="No workflows yet"
          description="Workflows define how your music is filtered, sorted, and assembled into playlists. Start from a template or build one from scratch."
          action={<NewWorkflowActions />}
        />
      )}

      {!isLoading && !isError && workflows.length > 0 && (
        <>
          <ResponsiveTable
            cards={
              <div className="flex flex-col gap-2">
                {workflows.map((wf) => (
                  <WorkflowRow
                    key={wf.id}
                    wf={wf}
                    runningWorkflowId={runningWorkflowId}
                    variant="card"
                    {...rowSchedule(wf.id)}
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
                    <TableHead className="w-40">Next Run</TableHead>
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
                      variant="table"
                      {...rowSchedule(wf.id)}
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
