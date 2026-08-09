import { AlertTriangle, Ban, Check, Clock, Play, X } from "lucide-react";

import { cn } from "#/lib/utils";

const STATUS_CONFIG: Record<
  string,
  { label: string; className: string; icon: React.ReactNode }
> = {
  pending: {
    label: "Pending",
    className: "bg-text-faint/15 text-text-faint",
    icon: <Clock className="size-2.5" />,
  },
  // Import-queue vocabulary: a file waiting its turn in the upload queue. It
  // has no run yet (no operation_runs row until it starts), so it sits muted
  // rather than borrowing any run status tone.
  queued: {
    label: "Queued",
    className: "bg-text-faint/15 text-text-faint",
    icon: <Clock className="size-2.5" />,
  },
  running: {
    label: "Running",
    className: "bg-primary/15 text-primary",
    icon: <Play className="size-2.5 fill-current" />,
  },
  // Workflow-run vocabulary: completed/failed/crashed.
  completed: {
    label: "Completed",
    className: "bg-status-connected/15 text-status-connected",
    icon: <Check className="size-2.5" />,
  },
  failed: {
    label: "Failed",
    className: "bg-destructive/15 text-destructive",
    icon: <X className="size-2.5" />,
  },
  // Crashed = the worker died, not the workflow logic. Distinct amber tone +
  // warning icon so triage can tell an operational event from a logic failure.
  crashed: {
    label: "Crashed",
    className: "bg-status-expired/15 text-status-expired",
    icon: <AlertTriangle className="size-2.5" />,
  },
  // OperationRun vocabulary: complete/partial/error/cancelled.
  complete: {
    label: "Complete",
    className: "bg-status-connected/15 text-status-connected",
    icon: <Check className="size-2.5" />,
  },
  // Partial = the run finished and did real work, but some items couldn't be
  // processed — expand the row to see exactly which. Shares the amber warning
  // tone with `crashed`: both say "it ran, but look at it", as opposed to the
  // destructive red of an outright failure. The label spells that out rather
  // than saying "Partial", which on its own doesn't tell you what happened.
  partial: {
    label: "Completed with issues",
    className: "bg-status-expired/15 text-status-expired",
    icon: <AlertTriangle className="size-2.5" />,
  },
  error: {
    label: "Error",
    className: "bg-destructive/15 text-destructive",
    icon: <X className="size-2.5" />,
  },
  cancelled: {
    label: "Cancelled",
    className: "bg-text-faint/15 text-text-muted",
    icon: <Ban className="size-2.5" />,
  },
};

export function getStatusConfig(status: string) {
  return STATUS_CONFIG[status] ?? STATUS_CONFIG.pending;
}

export function RunStatusBadge({
  status,
  className,
}: {
  status: string;
  className?: string;
}) {
  const config = getStatusConfig(status);
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-display font-medium",
        config.className,
        className,
      )}
    >
      {config.icon}
      {config.label}
    </span>
  );
}
