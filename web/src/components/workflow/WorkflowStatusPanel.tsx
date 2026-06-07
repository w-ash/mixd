/**
 * State-aware status region at the top of the workflow detail page.
 *
 * Replaces the old loose pipeline strip + last-run card. Three states, never
 * duplicating the run-history table below it:
 *   - ACTIVE  — a run is in flight (started here or reconnected after reload):
 *               live pipeline progress, connection liveness, and a link to the
 *               full live run. Leads with elapsed-since-start.
 *   - IDLE    — has runs, none active: the static pipeline (workflow identity),
 *               a definition-drift banner when the last run is stale, and an
 *               always-present cadence line. Last-run status/tracks/time live
 *               only in the history table (its first row IS the last run).
 *   - NEVER   — no runs yet: an inviting prompt with the static pipeline.
 */

import { AlertTriangle, CalendarClock, ExternalLink } from "lucide-react";
import { Link } from "react-router";

import type {
  LastRunSchema,
  WorkflowRunSummarySchema,
  WorkflowTaskDefSchemaInput,
} from "#/api/generated/model";
import { PipelineStrip } from "#/components/shared/PipelineStrip";
import { SSELivenessPill } from "#/components/shared/SSELivenessPill";
import type { SubProgressUpdate } from "#/hooks/useWorkflowSSE";
import { formatRelativeTime } from "#/lib/format";
import type { NodeStatus } from "#/lib/sse-types";

interface WorkflowStatusPanelProps {
  workflowId: string;
  tasks: WorkflowTaskDefSchemaInput[];
  lastRun: LastRunSchema | null;
  currentDefinitionVersion: number;
  /** The in-flight run for this workflow (server truth), or null when idle. */
  activeRun: WorkflowRunSummarySchema | null;
  nodeStatuses: Map<string, NodeStatus>;
  isExecuting: boolean;
  runAccepted: boolean;
  subProgress: SubProgressUpdate | null;
  /** In-session run id, used for the live-run link before `activeRun` lands. */
  runId: string | null;
  /** Pre-formatted next-run cadence line, or null when not scheduled. */
  nextRunLabel: string | null;
}

export function WorkflowStatusPanel({
  workflowId,
  tasks,
  lastRun,
  currentDefinitionVersion,
  activeRun,
  nodeStatuses,
  isExecuting,
  runAccepted,
  subProgress,
  runId,
  nextRunLabel,
}: WorkflowStatusPanelProps) {
  const isActive = isExecuting || activeRun !== null;
  const hasPipeline = tasks.length > 0;

  // ── ACTIVE ──────────────────────────────────────────────────────────────
  if (isActive) {
    const startedAt = activeRun?.started_at ?? activeRun?.created_at ?? null;
    const liveRunId = activeRun?.id ?? runId;

    return (
      <section className="mb-8 rounded-lg border-l-2 border-primary bg-surface-elevated/50 p-5">
        <div className="mb-3 flex items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <span className="relative flex size-2">
              <span className="absolute inline-flex size-full animate-ping rounded-full bg-primary/60" />
              <span className="relative inline-flex size-2 rounded-full bg-primary" />
            </span>
            <span className="font-display text-sm font-medium text-text">
              Running now
            </span>
            {startedAt && (
              <span className="text-xs text-text-faint">
                started {formatRelativeTime(startedAt)}
              </span>
            )}
          </div>
          {liveRunId && (
            <Link
              to={`/workflows/${workflowId}/runs/${liveRunId}`}
              className="inline-flex items-center gap-1 text-xs text-text-muted transition-colors hover:text-text"
            >
              View live run
              <ExternalLink className="size-2.5" />
            </Link>
          )}
        </div>

        {hasPipeline && (
          <div className="space-y-2">
            <PipelineStrip
              tasks={tasks}
              nodeStatuses={nodeStatuses}
              isExecuting={isActive}
              runAccepted={runAccepted}
              subProgress={subProgress}
            />
            <SSELivenessPill />
          </div>
        )}
      </section>
    );
  }

  // ── NEVER-RUN ───────────────────────────────────────────────────────────
  if (!lastRun) {
    return (
      <section className="mb-8 rounded-lg border border-border-muted bg-surface-elevated/50 p-5">
        {hasPipeline && (
          <div className="mb-3">
            <PipelineStrip tasks={tasks} />
          </div>
        )}
        <p className="font-display text-sm text-text-faint">
          Never run yet — run it to watch the pipeline live.
        </p>
      </section>
    );
  }

  // ── IDLE ────────────────────────────────────────────────────────────────
  const versionMismatch =
    lastRun.definition_version != null &&
    lastRun.definition_version < currentDefinitionVersion;

  return (
    <section className="mb-8 rounded-lg border border-border-muted bg-surface-elevated/50 p-5">
      {hasPipeline && (
        <div className="mb-3">
          <PipelineStrip tasks={tasks} />
        </div>
      )}

      <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2">
        <div className="flex items-center gap-1.5 text-xs text-text-muted">
          <CalendarClock className="size-3" />
          <span className="font-display">
            {nextRunLabel ?? "Not scheduled — run manually"}
          </span>
        </div>

        {versionMismatch && (
          <div className="flex items-center gap-1.5 text-xs text-primary">
            <AlertTriangle className="size-3" />
            <span className="font-display">
              Definition changed since last run
            </span>
          </div>
        )}
      </div>
    </section>
  );
}
