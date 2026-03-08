/**
 * Compact inline pipeline visualization — the "run-first" hero element.
 *
 * Shows workflow tasks as category-colored dots connected by thin lines.
 * During execution, dots animate through pending → running → completed states.
 * Replaces the full-height DAG on the workflow detail page.
 */

import { Check, Loader2, X } from "lucide-react";
import type { WorkflowTaskDefSchema } from "@/api/generated/model";
import type { NodeStatus } from "@/lib/sse-types";
import { cn } from "@/lib/utils";
import { getNodeCategory } from "@/lib/workflow-config";

interface PipelineStripProps {
  tasks: WorkflowTaskDefSchema[];
  nodeStatuses?: Map<string, NodeStatus>;
  className?: string;
}

function StatusOverlay({ status }: { status?: NodeStatus["status"] }) {
  if (!status || status === "pending") return null;

  if (status === "running") {
    return (
      <span className="absolute inset-0 flex items-center justify-center">
        <Loader2 size={12} className="animate-spin text-white" />
      </span>
    );
  }

  if (status === "completed") {
    return (
      <span className="absolute inset-0 flex items-center justify-center">
        <Check size={12} className="text-white" strokeWidth={3} />
      </span>
    );
  }

  // failed
  return (
    <span className="absolute inset-0 flex items-center justify-center">
      <X size={12} className="text-white" strokeWidth={3} />
    </span>
  );
}

export function PipelineStrip({
  tasks,
  nodeStatuses,
  className,
}: PipelineStripProps) {
  if (tasks.length === 0) return null;

  // During execution: find current step for progress description
  const currentStep = nodeStatuses
    ? [...nodeStatuses.values()].find((s) => s.status === "running")
    : undefined;
  const completedCount = nodeStatuses
    ? [...nodeStatuses.values()].filter((s) => s.status === "completed").length
    : 0;
  const isExecuting = nodeStatuses ? nodeStatuses.size > 0 : false;

  return (
    <div className={cn("space-y-3", className)}>
      {/* Dot chain */}
      <div className="flex items-center gap-0">
        {tasks.map((task, i) => {
          const config = getNodeCategory(task.type);
          const Icon = config.Icon;
          const status = nodeStatuses?.get(task.id);
          const statusState = status?.status;

          // Determine dot opacity/style based on execution state
          const isActive = statusState === "running";
          const isDone = statusState === "completed";
          const isFailed = statusState === "failed";

          return (
            <div key={task.id} className="flex items-center">
              {/* Connector line (not before first dot) */}
              {i > 0 && (
                <div
                  className={cn(
                    "h-px w-6 transition-colors duration-300",
                    isDone || isActive ? "bg-text-muted/50" : "bg-border-muted",
                  )}
                />
              )}

              {/* Node dot */}
              <div className="group relative flex flex-col items-center">
                <div
                  className={cn(
                    "relative flex size-7 items-center justify-center rounded-full transition-all duration-300",
                    isActive && "ring-2 ring-offset-1 ring-offset-surface",
                    isFailed &&
                      "ring-2 ring-destructive/50 ring-offset-1 ring-offset-surface",
                  )}
                  style={{
                    backgroundColor:
                      isDone || isActive || isFailed
                        ? config.accentColor
                        : `color-mix(in oklch, ${config.accentColor} 30%, oklch(0.18 0.01 60))`,
                    ...(isActive ? { ringColor: config.accentColor } : {}),
                  }}
                  title={`${config.label}: ${task.id}`}
                >
                  {statusState && statusState !== "pending" ? (
                    <StatusOverlay status={statusState} />
                  ) : (
                    <Icon
                      size={13}
                      style={{ color: isDone ? "white" : config.accentColor }}
                      className="transition-colors duration-300"
                    />
                  )}
                </div>

                {/* Label (below dot) */}
                <span className="mt-1.5 max-w-16 truncate text-center font-display text-[10px] text-text-faint">
                  {config.label}
                </span>
              </div>
            </div>
          );
        })}
      </div>

      {/* Progress bar during execution */}
      {isExecuting && (
        <div className="space-y-1.5">
          <div className="h-1 overflow-hidden rounded-full bg-surface-sunken">
            <div
              className="h-full rounded-full bg-primary transition-all duration-500 ease-out"
              style={{
                width: `${tasks.length > 0 ? (completedCount / tasks.length) * 100 : 0}%`,
              }}
            />
          </div>
          {currentStep && (
            <p className="font-display text-xs text-text-muted">
              Step {currentStep.executionOrder}/{currentStep.totalNodes}
              {" \u2014 "}
              {getNodeCategory(currentStep.nodeType).label}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
