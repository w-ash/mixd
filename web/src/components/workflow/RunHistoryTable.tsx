/**
 * Canonical execution-history ledger for a workflow.
 *
 * The single place a run's status/duration/tracks/time appears — the status
 * panel above never duplicates it. Row one is the most recent run, so when the
 * workflow is idle this table *is* the "last run" view. The "Started" column
 * uses relative time ("2h ago") for at-a-glance temporal context, falling back
 * to an absolute date past a week.
 */

import { Link } from "react-router";

import type { WorkflowRunSummarySchema } from "#/api/generated/model";
import { ResponsiveTable } from "#/components/shared/ResponsiveTable";
import { RunStatusBadge } from "#/components/shared/RunStatusBadge";
import { SectionHeader } from "#/components/shared/SectionHeader";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "#/components/ui/table";
import { formatDuration, formatRelativeTime } from "#/lib/format";

export function RunHistoryTable({
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
      <ResponsiveTable
        cards={
          <div className="flex flex-col gap-2">
            {runs.map((run) => (
              <article
                key={`${run.id}-card`}
                className="flex items-start gap-3 rounded-md border border-border bg-surface px-3 py-3"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <Link
                      to={`/workflows/${workflowId}/runs/${run.id}`}
                      className="font-mono text-sm text-text transition-colors hover:text-primary"
                    >
                      #{run.id}
                    </Link>
                    <RunStatusBadge status={run.status} />
                  </div>
                  <div className="mt-1 flex flex-wrap items-baseline gap-x-3 gap-y-1 font-mono text-xs text-text-muted">
                    <span>Duration {formatDuration(run.duration_ms)}</span>
                    <span>
                      {run.output_track_count ?? "—"} track
                      {run.output_track_count === 1 ? "" : "s"}
                    </span>
                    <span>
                      Started{" "}
                      {formatRelativeTime(run.started_at ?? run.created_at)}
                    </span>
                  </div>
                </div>
              </article>
            ))}
          </div>
        }
        table={
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
                    {run.output_track_count ?? "—"}
                  </TableCell>
                  <TableCell className="text-right text-xs text-text-muted">
                    {formatRelativeTime(run.started_at ?? run.created_at)}
                  </TableCell>
                  <TableCell className="text-right">
                    <Link
                      to={`/workflows/${workflowId}/runs/${run.id}`}
                      className="text-xs text-text-muted transition-colors hover:text-text"
                    >
                      View
                    </Link>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        }
      />
    </section>
  );
}
