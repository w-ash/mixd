import { AlertTriangle, GitBranch, Lock } from "lucide-react";
import { Link } from "react-router";

import { useListWorkflowsApiV1WorkflowsGet } from "@/api/generated/workflows/workflows";
import { PageHeader } from "@/components/layout/PageHeader";
import { EmptyState } from "@/components/shared/EmptyState";
import { getStatusConfig } from "@/components/shared/RunStatusBadge";
import { TablePagination } from "@/components/shared/TablePagination";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { usePagination } from "@/hooks/usePagination";
import { formatDate } from "@/lib/format";
import { cn } from "@/lib/utils";

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

export function Workflows() {
  const { page, limit, offset, setPage } = usePagination(0);

  const { data, isLoading, isError, error } = useListWorkflowsApiV1WorkflowsGet(
    { limit, offset },
  );

  const response = data?.status === 200 ? data.data : undefined;
  const workflows = response?.data ?? [];
  const total = response?.total ?? 0;
  const totalPages = total > 0 ? Math.ceil(total / limit) : 1;

  return (
    <div>
      <title>Workflows — Narada</title>
      <PageHeader
        title="Workflows"
        description="Declarative pipelines that compose your music criteria into playlists."
      />

      {isLoading && <WorkflowTableSkeleton />}

      {isError && (
        <EmptyState
          icon={<AlertTriangle className="size-10" />}
          heading="Failed to load workflows"
          description={
            error instanceof Error
              ? error.message
              : "An unexpected error occurred."
          }
        />
      )}

      {!isLoading && !isError && workflows.length === 0 && (
        <EmptyState
          icon={<GitBranch className="size-10" />}
          heading="No workflows yet"
          description="Workflows define how your music is filtered, sorted, and assembled into playlists."
        />
      )}

      {!isLoading && !isError && workflows.length > 0 && (
        <>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead className="w-20 text-right">Tasks</TableHead>
                <TableHead className="w-28">Last Run</TableHead>
                <TableHead className="w-36 text-right">Updated</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {workflows.map((wf) => {
                const lastRun = wf.last_run;
                const runConf = lastRun
                  ? getStatusConfig(lastRun.status)
                  : null;

                return (
                  <TableRow key={wf.id}>
                    <TableCell>
                      <div className="flex items-center gap-2">
                        <Link
                          to={`/workflows/${wf.id}`}
                          className="font-medium text-text hover:text-primary transition-colors"
                        >
                          {wf.name}
                        </Link>
                        {wf.is_template && (
                          <Badge
                            variant="outline"
                            className="gap-1 text-[10px]"
                          >
                            <Lock size={10} aria-hidden="true" />
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
                    <TableCell className="text-right tabular-nums">
                      {wf.task_count}
                    </TableCell>
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
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>

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
