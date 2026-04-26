import { ChevronDown } from "lucide-react";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router";
import type {
  OperationRunDetailSchema,
  OperationRunSummarySchema,
} from "#/api/generated/model";
import {
  useGetOperationRunApiV1OperationRunsRunIdGet,
  useListOperationRunsApiV1OperationRunsGet,
} from "#/api/generated/operation-runs/operation-runs";
import { PageHeader } from "#/components/layout/PageHeader";
import { EmptyState } from "#/components/shared/EmptyState";
import { RunStatusBadge } from "#/components/shared/RunStatusBadge";
import { Button } from "#/components/ui/button";
import { Skeleton } from "#/components/ui/skeleton";
import { formatDateTime } from "#/lib/format";
import { pluralize } from "#/lib/pluralize";
import { cn } from "#/lib/utils";

const OPERATION_LABELS: Record<string, string> = {
  import_lastfm_history: "Last.fm history import",
  import_spotify_likes: "Spotify likes import",
  export_lastfm_likes: "Last.fm likes export",
  import_spotify_history: "Spotify history import",
  import_connector_playlists: "Spotify playlist import",
  apply_assignments_bulk: "Apply all assignments",
};

function operationLabel(operationType: string): string {
  return OPERATION_LABELS[operationType] ?? operationType;
}

function CountsLine({
  counts,
}: {
  counts: OperationRunSummarySchema["counts"];
}) {
  const entries = Object.entries(counts).filter(
    ([, v]) => typeof v === "number",
  );
  if (entries.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1 font-mono text-xs text-text-muted">
      {entries.map(([k, v]) => (
        <span key={k}>
          {k}: <span className="text-text">{String(v)}</span>
        </span>
      ))}
    </div>
  );
}

function IssuesList({
  issues,
}: {
  issues: OperationRunDetailSchema["issues"];
}) {
  if (issues.length === 0) {
    return (
      <p className="text-sm text-text-muted">
        No issues recorded for this run.
      </p>
    );
  }
  return (
    <ul className="space-y-2">
      {issues.map((issue, i) => (
        <li
          // biome-ignore lint/suspicious/noArrayIndexKey: issue list is append-only and stable per render
          key={i}
          className="rounded border border-border bg-surface-sunken px-3 py-2 font-mono text-xs text-text"
        >
          <pre className="whitespace-pre-wrap break-words">
            {JSON.stringify(issue, null, 2)}
          </pre>
        </li>
      ))}
    </ul>
  );
}

function RunRowDetail({ runId }: { runId: string }) {
  const { data, isPending, isError } =
    useGetOperationRunApiV1OperationRunsRunIdGet(runId);

  if (isPending) {
    return (
      <div className="px-4 py-3">
        <Skeleton className="h-16 w-full" />
      </div>
    );
  }
  if (isError || data?.status !== 200) {
    return (
      <p className="px-4 py-3 text-sm text-destructive">
        Couldn't load run detail.
      </p>
    );
  }
  return (
    <div className="space-y-3 px-4 py-3">
      <CountsLine counts={data.data.counts} />
      <IssuesList issues={data.data.issues} />
    </div>
  );
}

export function ImportHistoryPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const deepLinkRunId = searchParams.get("run");
  const [expandedRunId, setExpandedRunId] = useState<string | null>(
    deepLinkRunId,
  );
  const [cursor, setCursor] = useState<string | null>(null);

  const { data, isPending, isError } =
    useListOperationRunsApiV1OperationRunsGet({
      limit: 20,
      ...(cursor !== null ? { cursor } : {}),
    });

  // Sync the deep-link from the URL into local state. We only listen for
  // URL changes — clicks update both ``expandedRunId`` and the URL via
  // the click handler, so this effect doesn't need to depend on
  // ``expandedRunId`` (and shouldn't, since that would re-fire on every
  // expand/collapse with redundant work).
  useEffect(() => {
    if (deepLinkRunId !== null) {
      setExpandedRunId(deepLinkRunId);
    }
  }, [deepLinkRunId]);

  const toggleExpand = (runId: string) => {
    const next = expandedRunId === runId ? null : runId;
    setExpandedRunId(next);
    setSearchParams(
      (prev) => {
        const params = new URLSearchParams(prev);
        if (next === null) params.delete("run");
        else params.set("run", next);
        return params;
      },
      { replace: true },
    );
  };

  const runs = data?.status === 200 ? data.data.data : [];
  const nextCursor = data?.status === 200 ? data.data.next_cursor : null;

  return (
    <div>
      <title>Import History — Mixd</title>
      <PageHeader
        title="Import History"
        description="Persistent log of every import, sync, and bulk apply you've kicked off. Expand any row to see counts and per-item issues."
      />

      {isPending && (
        <div className="space-y-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton
              // biome-ignore lint/suspicious/noArrayIndexKey: static skeleton
              key={i}
              className="h-16 w-full rounded-lg"
            />
          ))}
        </div>
      )}

      {isError && (
        <EmptyState
          heading="Couldn't load import history"
          description="Refresh the page or check your connection."
          role="alert"
        />
      )}

      {!isPending && !isError && runs.length === 0 && (
        <EmptyState
          heading="No imports yet"
          description="Run one from Settings → Integrations or the CLI, then come back to see the audit row."
        />
      )}

      {!isPending && !isError && runs.length > 0 && (
        <div className="overflow-hidden rounded-lg border border-border bg-surface-elevated shadow-elevated">
          <ul className="divide-y divide-border">
            {runs.map((run) => {
              const expanded = expandedRunId === run.id;
              return (
                <li key={run.id}>
                  <button
                    type="button"
                    className="flex w-full items-center gap-4 px-4 py-3 text-left transition-colors hover:bg-surface-sunken"
                    onClick={() => toggleExpand(run.id)}
                    aria-expanded={expanded}
                  >
                    <ChevronDown
                      className={cn(
                        "size-4 shrink-0 text-text-muted transition-transform duration-150",
                        expanded && "rotate-180",
                      )}
                      aria-hidden
                    />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="font-display font-medium text-text">
                          {operationLabel(run.operation_type)}
                        </span>
                        <RunStatusBadge status={run.status} />
                      </div>
                      <div className="font-mono text-xs text-text-muted">
                        Started {formatDateTime(run.started_at)}
                        {run.ended_at &&
                          ` · ended ${formatDateTime(run.ended_at)}`}
                      </div>
                    </div>
                    {run.issue_count > 0 && (
                      <span className="shrink-0 font-mono text-xs text-text-muted">
                        {pluralize(run.issue_count, "issue")}
                      </span>
                    )}
                  </button>
                  {expanded && <RunRowDetail runId={run.id} />}
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {nextCursor && (
        <div className="mt-6 flex justify-center">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setCursor(nextCursor)}
          >
            Load more
          </Button>
        </div>
      )}
    </div>
  );
}
