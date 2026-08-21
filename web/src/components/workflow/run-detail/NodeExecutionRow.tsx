import { ChevronDown, ChevronRight } from "lucide-react";
import { useCallback, useState } from "react";
import type { WorkflowRunNodeSchema } from "#/api/generated/model";
import { RunStatusBadge } from "#/components/shared/RunStatusBadge";
import { formatDuration } from "#/lib/format";
import { cn, numericCellClass } from "#/lib/utils";
import { getNodeCategory } from "#/lib/workflow-config";
import { PlaylistChangesPanel } from "./PlaylistChangesPanel";

/**
 * Desktop column template for the grid that hosts NodeExecutionRow items.
 * The rows subgrid into these tracks (order, name, status, duration,
 * counts, chevron) — keep this in sync with the cells rendered below.
 * Below `lg:` the parent falls back to one column and each row lays out
 * as a wrapping flex line instead.
 */
export const NODE_ROW_GRID_COLS =
  "lg:grid-cols-[auto_minmax(0,1fr)_auto_auto_auto_auto]";

/** Single node row with expand/collapse for details. */
export function NodeExecutionRow({ node }: { node: WorkflowRunNodeSchema }) {
  const [expanded, setExpanded] = useState(false);
  const hasDetails = Boolean(node.node_details?.playlist_changes);

  const categoryConfig = getNodeCategory(node.node_type);

  const toggle = useCallback(() => {
    if (hasDetails) setExpanded((prev) => !prev);
  }, [hasDetails]);

  const containerClass = cn(
    "col-span-full flex flex-wrap items-center gap-x-4 gap-y-2",
    "lg:grid lg:grid-cols-subgrid",
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
      <span className="font-mono text-xs text-text-faint">
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
      <RunStatusBadge status={node.status} className="justify-self-end" />
      <span className={numericCellClass}>
        {formatDuration(node.duration_ms || undefined)}
      </span>
      {node.input_track_count != null && node.output_track_count != null && (
        <span className={cn(numericCellClass, "lg:col-start-5")}>
          {node.input_track_count} &rarr; {node.output_track_count}
        </span>
      )}
      {hasDetails && (
        <span className="text-text-faint lg:col-start-6">
          {expanded ? (
            <ChevronDown className="size-3.5" />
          ) : (
            <ChevronRight className="size-3.5" />
          )}
        </span>
      )}
      {expanded && (
        <div className="w-full lg:col-span-full">
          <PlaylistChangesPanel node={node} />
        </div>
      )}
    </div>
  );
}
