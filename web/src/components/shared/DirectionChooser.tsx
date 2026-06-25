import { useId } from "react";

import {
  describeDirection,
  formatDirectionLabel,
  type SyncDirection,
} from "#/lib/sync-direction";
import { cn } from "#/lib/utils";

interface DirectionChooserProps {
  value: SyncDirection;
  onChange: (direction: SyncDirection) => void;
  connectorLabel: string;
  disabled?: boolean;
  className?: string;
  /** Accessible group label (default "Sync direction"). */
  legend?: string;
}

// pull-first: the additive, non-destructive-of-Mixd default leads.
const DIRECTIONS: readonly SyncDirection[] = ["pull", "push"];

/**
 * Interactive push/pull picker — the one direction control across the import,
 * link, and sync flows. Each option leads with its `direction_label` (what gets
 * overwritten) plus a plain-language line on which side wins.
 */
export function DirectionChooser({
  value,
  onChange,
  connectorLabel,
  disabled,
  className,
  legend = "Sync direction",
}: DirectionChooserProps) {
  // Unique radio-group name so multiple choosers on one page don't collide.
  const groupName = useId();

  return (
    <fieldset className={cn("space-y-2", className)} disabled={disabled}>
      <legend className="font-display text-sm font-medium text-text">
        {legend}
      </legend>
      {DIRECTIONS.map((dir) => {
        const selected = value === dir;
        return (
          <label
            key={dir}
            className={cn(
              "flex items-start gap-3 rounded-md border p-3 transition-colors",
              disabled
                ? "cursor-not-allowed opacity-60"
                : "cursor-pointer hover:bg-accent/30",
              selected ? "border-primary bg-primary/5" : "border-border",
            )}
          >
            <input
              type="radio"
              name={groupName}
              value={dir}
              checked={selected}
              onChange={() => onChange(dir)}
              disabled={disabled}
              className="mt-1"
            />
            <span>
              <span className="block font-medium text-text">
                {formatDirectionLabel(dir, connectorLabel)}
              </span>
              <span className="mt-0.5 block text-xs text-text-muted">
                {describeDirection(dir, connectorLabel)}
              </span>
            </span>
          </label>
        );
      })}
    </fieldset>
  );
}
