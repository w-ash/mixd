import type { OperationProgress as OperationProgressData } from "@/hooks/useOperationProgress";
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
    icon: "...",
    label: "Waiting",
    barClass: "bg-text-faint",
    iconBg: "bg-text-faint/20",
    iconText: "text-text-faint",
  },
  running: {
    icon: "▶",
    label: "Running",
    barClass: "bg-primary",
    iconBg: "bg-primary/20",
    iconText: "text-primary",
  },
  completed: {
    icon: "✓",
    label: "Complete",
    barClass: "bg-status-connected",
    iconBg: "bg-status-connected/20",
    iconText: "text-status-connected",
  },
  failed: {
    icon: "!",
    label: "Failed",
    barClass: "bg-destructive",
    iconBg: "bg-destructive/20",
    iconText: "text-destructive",
  },
  cancelled: {
    icon: "×",
    label: "Cancelled",
    barClass: "bg-text-faint",
    iconBg: "bg-text-faint/20",
    iconText: "text-text-faint",
  },
} as const;

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
              "flex size-5 shrink-0 items-center justify-center rounded-full text-xs leading-none font-bold [text-box:trim-both_cap_alphabetic]",
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
    </output>
  );
}
