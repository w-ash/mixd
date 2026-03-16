import {
  AlertTriangle,
  CheckCircle2,
  Circle,
  Info,
  XCircle,
} from "lucide-react";
import { cn } from "@/lib/utils";

type StatusVariant = "success" | "warning" | "error" | "info" | "neutral";

interface StatusIndicatorProps {
  variant: StatusVariant;
  label: string;
  detail?: string;
  size?: "sm" | "md";
}

export const variantColorClass: Record<StatusVariant, string> = {
  success: "text-status-connected",
  warning: "text-status-expired",
  error: "text-destructive",
  info: "text-status-syncing",
  neutral: "text-text-muted",
};

const variantConfig = {
  success: {
    icon: CheckCircle2,
    colorClass: "text-status-connected",
  },
  warning: {
    icon: AlertTriangle,
    colorClass: "text-status-expired",
  },
  error: {
    icon: XCircle,
    colorClass: "text-destructive",
  },
  info: {
    icon: Info,
    colorClass: "text-status-syncing",
  },
  neutral: {
    icon: Circle,
    colorClass: "text-text-muted",
  },
} as const;

/** Map a match confidence score (0-100) to a status variant. */
export function confidenceVariant(confidence: number): StatusVariant {
  if (confidence >= 80) return "success";
  if (confidence >= 50) return "warning";
  return "error";
}

/** Map a sync status string to a status variant. */
export function syncStatusVariant(status: string): StatusVariant {
  switch (status) {
    case "synced":
      return "success";
    case "syncing":
      return "info";
    case "error":
      return "error";
    default:
      return "neutral";
  }
}

/**
 * Semantic status indicator combining icon + color + text label.
 * Never displays a bare colored dot — always provides at least icon + text.
 */
export function StatusIndicator({
  variant,
  label,
  detail,
  size = "sm",
}: StatusIndicatorProps) {
  const config = variantConfig[variant];
  const Icon = config.icon;
  const iconSize = size === "sm" ? "size-3" : "size-3.5";

  return (
    <span className={cn("inline-flex items-center gap-1.5", config.colorClass)}>
      <Icon className={iconSize} />
      <span className={cn("font-body", size === "sm" ? "text-xs" : "text-sm")}>
        {label}
      </span>
      {detail && (
        <span className="text-text-faint font-body text-xs">{detail}</span>
      )}
    </span>
  );
}
