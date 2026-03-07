import { ArrowLeft, HelpCircle, Lock } from "lucide-react";
import { Link, useParams } from "react-router";

import { useGetWorkflowApiV1WorkflowsWorkflowIdGet } from "@/api/generated/workflows/workflows";
import { PageHeader } from "@/components/layout/PageHeader";
import { EmptyState } from "@/components/shared/EmptyState";
import { NodeTypeBadge } from "@/components/shared/NodeTypeBadge";
import { WorkflowGraph } from "@/components/shared/WorkflowGraph";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { formatDate } from "@/lib/format";

function DetailSkeleton() {
  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-4 w-96" />
      </div>
      <Skeleton className="h-[500px] w-full rounded-lg" />
    </div>
  );
}

export function WorkflowDetail() {
  const { id } = useParams<{ id: string }>();
  const workflowId = Number(id);

  const { data, isLoading, isError } =
    useGetWorkflowApiV1WorkflowsWorkflowIdGet(workflowId);

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
  const nodeTypes = [...new Set(tasks.map((t) => t.type))];

  return (
    <div>
      <Link
        to="/workflows"
        className="mb-4 inline-flex items-center gap-1.5 text-sm text-text-muted hover:text-text transition-colors"
      >
        <ArrowLeft size={14} />
        Workflows
      </Link>

      <PageHeader
        title={workflow.name}
        description={workflow.description ?? undefined}
        action={
          workflow.is_template ? (
            <Badge variant="outline" className="gap-1">
              <Lock size={12} aria-hidden="true" />
              Template
            </Badge>
          ) : undefined
        }
      />

      <div className="mb-6 flex items-center gap-3 text-sm text-text-muted">
        <span>
          {workflow.task_count} {workflow.task_count === 1 ? "task" : "tasks"}
        </span>
        {nodeTypes.length > 0 && (
          <>
            <span aria-hidden="true">&middot;</span>
            <span className="flex items-center gap-1.5">
              {nodeTypes.map((nt) => (
                <NodeTypeBadge key={nt} nodeType={nt} />
              ))}
            </span>
          </>
        )}
        {workflow.updated_at && (
          <>
            <span aria-hidden="true">&middot;</span>
            <span>Updated {formatDate(workflow.updated_at)}</span>
          </>
        )}
      </div>

      {tasks.length > 0 ? (
        <div className="h-[500px] rounded-lg border border-border-muted bg-surface-sunken">
          <WorkflowGraph tasks={tasks} />
        </div>
      ) : (
        <EmptyState
          heading="No tasks defined"
          description="This workflow has no tasks in its definition."
        />
      )}
    </div>
  );
}
