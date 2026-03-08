import { Handle, Position } from "@xyflow/react";
import type { LucideIcon } from "lucide-react";

import type { NodeExecutionStatus } from "@/lib/sse-types";
import { cn } from "@/lib/utils";
import type { DiffStatus } from "@/lib/workflow-diff";

export interface WorkflowNodeData {
  taskId: string;
  nodeType: string;
  config: Record<string, unknown>;
  executionStatus?: NodeExecutionStatus;
  outputTrackCount?: number;
  inputTrackCount?: number;
  errorMessage?: string;
  mode?: "view" | "edit";
  selected?: boolean;
  diffStatus?: DiffStatus;
}

interface BaseWorkflowNodeProps {
  data: WorkflowNodeData;
  Icon: LucideIcon;
  accentColor: string;
  label: string;
}

const STATUS_STYLES: Record<
  NodeExecutionStatus,
  { border: string; accent: string; glow?: string }
> = {
  pending: {
    border: "border-border border-dashed opacity-60",
    accent: "var(--color-node-pending)",
  },
  running: {
    border: "border-primary/50 shadow-glow",
    accent: "var(--color-node-running)",
    glow: "animate-pulse",
  },
  completed: {
    border: "border-status-connected/40",
    accent: "var(--color-node-completed)",
  },
  failed: {
    border: "border-destructive/40",
    accent: "var(--color-node-failed)",
  },
};

export function BaseWorkflowNode({
  data,
  Icon,
  accentColor,
  label,
}: BaseWorkflowNodeProps) {
  const configEntries = Object.entries(data.config)
    .slice(0, 3)
    .map(([k, v]) => ({ key: k, value: String(v) }));

  const status = data.executionStatus;
  const statusStyle = status ? STATUS_STYLES[status] : undefined;
  const resolvedAccent = statusStyle?.accent ?? accentColor;

  const diffBorder =
    data.diffStatus === "added"
      ? "ring-2 ring-status-connected/60"
      : data.diffStatus === "removed"
        ? "ring-2 ring-destructive/60 opacity-50 line-through"
        : data.diffStatus === "modified"
          ? "ring-2 ring-[oklch(0.8_0.14_85)]/60"
          : undefined;

  const isEditMode = data.mode === "edit";
  const hideTarget = isEditMode && data.nodeType.startsWith("source");
  const hideSource = isEditMode && data.nodeType.startsWith("destination");

  return (
    <>
      {!hideTarget && (
        <Handle type="target" position={Position.Left} className="!bg-border" />
      )}
      <div
        className={cn(
          "relative flex min-w-[180px] max-w-[280px] items-start gap-3 rounded-lg border bg-surface-elevated px-3 py-2.5 shadow-elevated transition-all duration-300",
          statusStyle?.border ?? "border-border",
          statusStyle?.glow,
          data.selected && "ring-2 ring-primary",
          diffBorder,
        )}
      >
        <div
          className="absolute left-0 top-2 bottom-2 w-0.5 rounded-full transition-colors duration-300"
          style={{ backgroundColor: resolvedAccent }}
        />
        <div
          className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-md transition-colors duration-300"
          style={{
            backgroundColor: `color-mix(in oklch, ${resolvedAccent} 20%, transparent)`,
          }}
        >
          <Icon
            size={15}
            strokeWidth={1.5}
            style={{ color: resolvedAccent }}
            aria-hidden="true"
          />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-1.5">
            <span className="font-display text-xs font-medium text-text">
              {label}
            </span>
            <span className="font-mono text-[10px] text-text-faint">
              {data.taskId}
            </span>
          </div>
          {/* Execution status line — track counts or error */}
          {status === "completed" &&
          data.inputTrackCount != null &&
          data.outputTrackCount != null ? (
            <p className="mt-0.5 font-mono text-[10px] text-status-connected">
              {data.inputTrackCount} &rarr; {data.outputTrackCount} tracks
            </p>
          ) : status === "failed" && data.errorMessage ? (
            <p
              className="mt-0.5 truncate font-mono text-[10px] text-destructive"
              title={data.errorMessage}
            >
              {data.errorMessage}
            </p>
          ) : configEntries.length > 0 ? (
            <div className="mt-1 space-y-0.5">
              {configEntries.map(({ key, value }) => (
                <p
                  key={key}
                  className="truncate font-mono text-[10px] text-text-muted"
                >
                  <span className="text-text-faint">{key}:</span> {value}
                </p>
              ))}
            </div>
          ) : null}
        </div>
      </div>
      {!hideSource && (
        <Handle
          type="source"
          position={Position.Right}
          className="!bg-border"
        />
      )}
    </>
  );
}
