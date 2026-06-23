import { ArrowRight } from "lucide-react";

import { formatDirectionLabel, type SyncDirection } from "#/lib/sync-direction";
import { cn } from "#/lib/utils";

interface SyncDirectionIndicatorProps {
  direction: SyncDirection;
  connectorLabel: string;
  /**
   * Pre-formatted label from the API (`direction_label`). Used verbatim when
   * present; otherwise the client formatter derives it from `direction`.
   */
  label?: string;
  className?: string;
}

/**
 * Read-only sync-direction display. Leads with the source → target and names
 * the overwritten side — the one consistent direction vocabulary, paired with
 * {@link DirectionChooser} for the interactive case.
 */
export function SyncDirectionIndicator({
  direction,
  connectorLabel,
  label,
  className,
}: SyncDirectionIndicatorProps) {
  const text =
    label && label.length > 0
      ? label
      : formatDirectionLabel(direction, connectorLabel);

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 font-body text-sm text-text-muted",
        className,
      )}
    >
      <ArrowRight className="size-3.5 shrink-0 text-text-faint" aria-hidden />
      {text}
    </span>
  );
}
