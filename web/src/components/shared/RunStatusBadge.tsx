import { Check, Clock, Play, X } from "lucide-react";

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
  running: {
    label: "Running",
    className: "bg-primary/15 text-primary",
    icon: <Play className="size-2.5 fill-current" />,
  },
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
