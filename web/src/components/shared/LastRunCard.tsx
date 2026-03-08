/**
 * Compact card showing the latest workflow run status.
 *
 * Displays status badge, duration, output track count, and completion time.
 * Shows a version mismatch warning when the run was executed against an
 * older workflow definition than the current one.
 */

import { AlertTriangle, ExternalLink } from "lucide-react";
import { Link } from "react-router";

import type { LastRunSchema } from "@/api/generated/model";
import { RunStatusBadge } from "@/components/shared/RunStatusBadge";
import { formatDate } from "@/lib/format";

interface LastRunCardProps {
  run: LastRunSchema | null;
  currentDefinitionVersion: number;
  workflowId: number;
}

export function LastRunCard({
  run,
  currentDefinitionVersion,
  workflowId,
}: LastRunCardProps) {
  if (!run) {
    return (
      <div className="rounded-lg border border-border-muted bg-surface-elevated/50 px-4 py-3">
        <p className="font-display text-sm text-text-faint">No runs yet</p>
      </div>
    );
  }

  const versionMismatch =
    run.definition_version != null &&
    run.definition_version < currentDefinitionVersion;

  return (
    <div className="rounded-lg border-l-2 border-border-muted bg-surface-elevated/50 pl-4 pr-4 py-3">
      {versionMismatch && (
        <div className="mb-2 flex items-center gap-1.5 text-xs text-primary">
          <AlertTriangle size={12} />
          <span className="font-display">
            Definition changed since last run
          </span>
        </div>
      )}

      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <RunStatusBadge status={run.status} />
          {run.output_track_count != null && (
            <span className="font-mono text-xs text-text-muted">
              {run.output_track_count} tracks
            </span>
          )}
          {run.completed_at && (
            <span className="text-xs text-text-faint">
              {formatDate(run.completed_at)}
            </span>
          )}
        </div>

        <Link
          to={`/workflows/${workflowId}/runs/${run.id}`}
          className="inline-flex items-center gap-1 text-xs text-text-muted hover:text-text transition-colors"
        >
          Details
          <ExternalLink size={10} />
        </Link>
      </div>
    </div>
  );
}
