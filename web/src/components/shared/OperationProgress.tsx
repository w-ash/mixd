import { AlertTriangle, Check, Ellipsis, Play, X } from "lucide-react";
import type {
  OperationProgress as OperationProgressData,
  SubOperationProgress,
} from "@/hooks/useOperationProgress";
import { cn } from "@/lib/utils";

function formatEta(seconds: number): string {
  if (seconds < 60) return `${Math.ceil(seconds)}s`;
  const mins = Math.floor(seconds / 60);
  const secs = Math.ceil(seconds % 60);
  return secs > 0 ? `${mins}m ${secs}s` : `${mins}m`;
}

function formatRate(rate: number): string {
  return rate >= 1 ? `${rate.toFixed(1)}/s` : `${(rate * 60).toFixed(1)}/min`;
}

const statusConfig = {
  pending: {
    icon: <Ellipsis className="size-3" />,
    label: "Waiting",
    barClass: "bg-text-faint",
    iconBg: "bg-text-faint/20",
    iconText: "text-text-faint",
  },
  running: {
    icon: <Play className="size-3 fill-current" />,
    label: "Running",
    barClass: "bg-primary",
    iconBg: "bg-primary/20",
    iconText: "text-primary",
  },
  completed: {
    icon: <Check className="size-3" />,
    label: "Complete",
    barClass: "bg-status-connected",
    iconBg: "bg-status-connected/20",
    iconText: "text-status-connected",
  },
  failed: {
    icon: <AlertTriangle className="size-3" />,
    label: "Failed",
    barClass: "bg-destructive",
    iconBg: "bg-destructive/20",
    iconText: "text-destructive",
  },
  cancelled: {
    icon: <X className="size-3" />,
    label: "Cancelled",
    barClass: "bg-text-faint",
    iconBg: "bg-text-faint/20",
    iconText: "text-text-faint",
  },
};

interface OperationProgressProps {
  progress: OperationProgressData;
  className?: string;
}

export function OperationProgress({
  progress,
  className,
}: OperationProgressProps) {
  const config = statusConfig[progress.status];
  const isTerminal =
    progress.status === "completed" ||
    progress.status === "failed" ||
    progress.status === "cancelled";
  const percentage = progress.completionPercentage ?? 0;

  return (
    <output
      className={cn("block space-y-2", className)}
      aria-live="polite"
      aria-label={`Operation ${config.label}: ${progress.message}`}
    >
      {/* Status + message row */}
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 min-w-0">
          <span
            className={cn(
              "flex size-5 shrink-0 items-center justify-center rounded-full",
              config.iconBg,
              config.iconText,
            )}
            aria-hidden="true"
          >
            {config.icon}
          </span>
          <span className="truncate text-sm text-text">{progress.message}</span>
        </div>

        {/* Metrics */}
        <div className="flex shrink-0 items-center gap-3 text-xs tabular-nums text-text-muted font-mono">
          {progress.total !== null && (
            <span>
              {progress.current}/{progress.total}
            </span>
          )}
          {progress.itemsPerSecond !== null && (
            <span>{formatRate(progress.itemsPerSecond)}</span>
          )}
          {progress.etaSeconds !== null && !isTerminal && (
            <span>~{formatEta(progress.etaSeconds)}</span>
          )}
          {progress.completionPercentage !== null && (
            <span>{Math.round(progress.completionPercentage)}%</span>
          )}
        </div>
      </div>

      {/* Progress bar */}
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-surface-elevated">
        <div
          className={cn(
            "h-full rounded-full transition-all duration-300 ease-out",
            config.barClass,
            progress.status === "pending" && "animate-pulse w-full opacity-30",
          )}
          style={
            progress.status !== "pending"
              ? { width: `${Math.max(percentage, isTerminal ? 100 : 2)}%` }
              : undefined
          }
        />
      </div>

      {/* Sub-operation progress */}
      {progress.subOperation && (
        <SubOperationBar subOperation={progress.subOperation} />
      )}
    </output>
  );
}

function SubOperationBar({
  subOperation,
}: {
  subOperation: SubOperationProgress;
}) {
  const subPercentage = subOperation.completionPercentage ?? 0;
  const isDeterminate = subOperation.total !== null;

  return (
    <output
      className="ml-4 block space-y-1 border-l-2 border-text-faint/20 pl-3"
      aria-label={`Sub-operation: ${subOperation.message}`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-xs text-text-muted">
          {subOperation.message}
        </span>
        {isDeterminate && subOperation.total !== null && (
          <span className="shrink-0 text-xs tabular-nums text-text-faint font-mono">
            {subOperation.current}/{subOperation.total}
          </span>
        )}
      </div>
      <div className="h-1 w-full overflow-hidden rounded-full bg-surface-elevated">
        <div
          className={cn(
            "h-full rounded-full transition-all duration-300 ease-out bg-primary/60",
            !isDeterminate && "animate-pulse w-full opacity-40",
          )}
          style={
            isDeterminate
              ? { width: `${Math.max(subPercentage, 2)}%` }
              : undefined
          }
        />
      </div>
    </output>
  );
}
