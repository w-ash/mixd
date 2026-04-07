/**
 * Compact inline pipeline visualization — the "run-first" hero element.
 *
 * Shows workflow tasks as category-colored dots connected by thin lines.
 * During execution, dots animate through pending → running → completed states.
 * Replaces the full-height DAG on the workflow detail page.
 */

import { Check, Loader2, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import type { WorkflowTaskDefSchema } from "#/api/generated/model";
import type { NodeStatus } from "#/lib/sse-types";
import { cn } from "#/lib/utils";
import { getNodeCategory } from "#/lib/workflow-config";

interface PipelineStripProps {
  tasks: WorkflowTaskDefSchema[];
  nodeStatuses?: Map<string, NodeStatus>;
  isExecuting?: boolean;
  className?: string;
}

function StatusOverlay({ status }: { status?: NodeStatus["status"] }) {
  if (!status || status === "pending") return null;

  if (status === "running") {
    return (
      <span className="absolute inset-0 flex items-center justify-center">
        <Loader2 className="size-3 animate-spin text-white" />
      </span>
    );
  }

  if (status === "completed") {
    return (
      <span className="absolute inset-0 flex items-center justify-center">
        <Check className="size-3 text-white" strokeWidth={3} />
      </span>
    );
  }

  // failed
  return (
    <span className="absolute inset-0 flex items-center justify-center">
      <X className="size-3 text-white" strokeWidth={3} />
    </span>
  );
}

export function PipelineStrip({
  tasks,
  nodeStatuses,
  isExecuting = false,
  className,
}: PipelineStripProps) {
  // During execution: find current step for progress description
  const currentStep = nodeStatuses
    ? [...nodeStatuses.values()].find((s) => s.status === "running")
    : undefined;
  const completedCount = nodeStatuses
    ? [...nodeStatuses.values()].filter((s) => s.status === "completed").length
    : 0;
  const hasStatuses = nodeStatuses ? nodeStatuses.size > 0 : false;
  const showProgress = isExecuting || hasStatuses;

  // Auto-scroll the active (running) node into view
  const activeNodeRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const [canScrollLeft, setCanScrollLeft] = useState(false);
  const [canScrollRight, setCanScrollRight] = useState(false);

  const updateScrollEdges = useCallback(() => {
    const el = scrollRef.current;
    if (!el || tasks.length === 0) return;
    setCanScrollLeft(el.scrollLeft > 0);
    setCanScrollRight(el.scrollLeft + el.clientWidth < el.scrollWidth - 1);
  }, [tasks.length]);

  const activeNodeId = currentStep?.nodeId;
  useEffect(() => {
    // Re-runs when active node changes — activeNodeId triggers the effect
    if (activeNodeId) {
      activeNodeRef.current?.scrollIntoView({
        behavior: "smooth",
        block: "nearest",
        inline: "center",
      });
    }
    // Defer edge update to let scrollIntoView settle
    const id = setTimeout(updateScrollEdges, 350);
    return () => clearTimeout(id);
  }, [activeNodeId, updateScrollEdges]);

  // Detect overflow on mount and resize
  useEffect(() => {
    updateScrollEdges();
    const observer = new ResizeObserver(updateScrollEdges);
    if (scrollRef.current) observer.observe(scrollRef.current);
    return () => observer.disconnect();
  }, [updateScrollEdges]);

  if (tasks.length === 0) return null;

  return (
    <div className={cn("space-y-3", className)}>
      {/* Dot chain — scrollable with edge fade affordance */}
      <div className="relative">
        {canScrollLeft && (
          <div className="pointer-events-none absolute inset-y-0 left-0 z-10 w-8 bg-gradient-to-r from-surface to-transparent" />
        )}
        {canScrollRight && (
          <div className="pointer-events-none absolute inset-y-0 right-0 z-10 w-8 bg-gradient-to-l from-surface to-transparent" />
        )}
        <div
          ref={scrollRef}
          className="scrollbar-hide overflow-x-auto"
          onScroll={updateScrollEdges}
        >
          <div className="flex w-max items-center gap-0">
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
                <div
                  key={task.id}
                  ref={isActive ? activeNodeRef : undefined}
                  className="flex items-center"
                >
                  {/* Connector line (not before first dot) */}
                  {i > 0 && (
                    <div
                      className={cn(
                        "h-px w-6 transition-colors duration-300",
                        isDone || isActive
                          ? "bg-text-muted/50"
                          : "bg-border-muted",
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
                          style={{
                            color: isDone ? "white" : config.accentColor,
                          }}
                          className="size-3.5 transition-colors duration-300"
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
        </div>
      </div>

      {/* Progress bar during execution */}
      {showProgress && (
        <div className="space-y-1.5">
          <div className="h-1 overflow-hidden rounded-full bg-surface-sunken">
            <div
              className="h-full rounded-full bg-primary transition-all duration-500 ease-out"
              style={{
                width: `${tasks.length > 0 ? (completedCount / tasks.length) * 100 : 0}%`,
              }}
            />
          </div>
          {currentStep ? (
            <p className="font-display text-xs text-text-muted">
              Step {currentStep.executionOrder}/{currentStep.totalNodes}
              {" \u2014 "}
              {getNodeCategory(currentStep.nodeType).label}
            </p>
          ) : !hasStatuses ? (
            <p className="font-display text-xs text-text-muted animate-text-breathe">
              Initializing…
            </p>
          ) : null}
        </div>
      )}
    </div>
  );
}
