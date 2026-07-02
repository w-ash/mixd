import { ChevronDown, ChevronRight } from "lucide-react";
import { useCallback, useState } from "react";
import type { WorkflowRunNodeSchema } from "#/api/generated/model";
import { RunStatusBadge } from "#/components/shared/RunStatusBadge";
import { formatDuration } from "#/lib/format";
import { cn } from "#/lib/utils";
import { getNodeCategory } from "#/lib/workflow-config";
import { PlaylistChangesPanel } from "./PlaylistChangesPanel";

/** Single node row with expand/collapse for details. */
export function NodeExecutionRow({ node }: { node: WorkflowRunNodeSchema }) {
  const [expanded, setExpanded] = useState(false);
  const hasDetails = Boolean(node.node_details?.playlist_changes);

  const categoryConfig = getNodeCategory(node.node_type);

  const toggle = useCallback(() => {
    if (hasDetails) setExpanded((prev) => !prev);
  }, [hasDetails]);

  const containerClass = cn(
    "rounded-lg border border-border bg-surface-elevated px-4 py-3",
    hasDetails && "cursor-pointer",
  );

  const interactiveProps = hasDetails
    ? {
        onClick: toggle,
        onKeyDown: (e: React.KeyboardEvent) => {
          if (e.key === "Enter" || e.key === " ") toggle();
        },
        role: "button" as const,
        tabIndex: 0,
      }
    : {};

  return (
    <div className={containerClass} {...interactiveProps}>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <span className="w-6 shrink-0 font-mono text-xs text-text-faint">
          {node.execution_order}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-display text-sm font-medium text-text">
              {node.node_id}
            </span>
            <span
              className="rounded-full px-1.5 py-0.5 font-display text-[10px]"
              style={{
                backgroundColor: `color-mix(in oklch, ${categoryConfig.accentColor} 20%, transparent)`,
                color: categoryConfig.accentColor,
              }}
            >
              {categoryConfig.label}
            </span>
          </div>
          {node.error_message && (
            <p className="mt-0.5 truncate text-xs text-destructive">
              {node.error_message}
            </p>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <RunStatusBadge status={node.status} />
          <span className="w-16 text-right font-mono text-xs tabular-nums text-text-muted">
            {formatDuration(node.duration_ms || undefined)}
          </span>
          {node.input_track_count != null &&
            node.output_track_count != null && (
              <span className="font-mono text-xs tabular-nums text-text-muted">
                {node.input_track_count} &rarr; {node.output_track_count}
              </span>
            )}
          {hasDetails && (
            <span className="text-text-faint">
              {expanded ? (
                <ChevronDown className="size-3.5" />
              ) : (
                <ChevronRight className="size-3.5" />
              )}
            </span>
          )}
        </div>
      </div>
      {expanded && <PlaylistChangesPanel node={node} />}
    </div>
  );
}
