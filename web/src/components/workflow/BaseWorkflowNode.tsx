import { Handle, Position } from "@xyflow/react";
import type { LucideIcon } from "lucide-react";

export interface WorkflowNodeData {
  taskId: string;
  nodeType: string;
  config: Record<string, unknown>;
}

interface BaseWorkflowNodeProps {
  data: WorkflowNodeData;
  Icon: LucideIcon;
  accentColor: string;
  label: string;
}

export function BaseWorkflowNode({
  data,
  Icon,
  accentColor,
  label,
}: BaseWorkflowNodeProps) {
  const configSummary = Object.entries(data.config)
    .slice(0, 2)
    .map(([k, v]) => `${k}: ${String(v)}`)
    .join(", ");

  return (
    <>
      <Handle type="target" position={Position.Left} className="!bg-border" />
      <div className="relative flex min-w-[200px] items-start gap-3 rounded-lg border border-border bg-surface-elevated px-3 py-2.5 shadow-elevated">
        <div
          className="absolute left-0 top-2 bottom-2 w-0.5 rounded-full"
          style={{ backgroundColor: accentColor }}
        />
        <div
          className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-md"
          style={{
            backgroundColor: `color-mix(in oklch, ${accentColor} 20%, transparent)`,
          }}
        >
          <Icon
            size={15}
            strokeWidth={1.5}
            style={{ color: accentColor }}
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
          {configSummary && (
            <p className="mt-0.5 truncate font-mono text-[10px] text-text-muted">
              {configSummary}
            </p>
          )}
        </div>
      </div>
      <Handle type="source" position={Position.Right} className="!bg-border" />
    </>
  );
}
