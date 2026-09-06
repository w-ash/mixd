import { X } from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "#/components/ui/button";
import { pluralize } from "#/lib/pluralize";
import { cn } from "#/lib/utils";

interface BulkSelectionBarProps {
  /** Number of selected rows. The bar renders nothing at 0. */
  count: number;
  onClear: () => void;
  /** Names the selected rows ("3 tracks selected"). Omit for a bare "3 selected". */
  noun?: string;
  /** Bulk actions for the current selection, rendered between count and Clear. */
  children?: ReactNode;
  className?: string;
}

/**
 * Toolbar shown above a multi-select list while a selection exists: the
 * selected count, the caller's bulk actions, and a Clear escape hatch.
 */
export function BulkSelectionBar({
  count,
  onClear,
  noun,
  children,
  className,
}: BulkSelectionBarProps) {
  if (count === 0) return null;

  return (
    <section
      aria-label="Bulk selection"
      className={cn(
        "mb-3 flex items-center gap-3 rounded-md border border-primary/40 bg-primary/5 px-3 py-2 text-sm",
        className,
      )}
    >
      <span className="font-display text-text">
        {noun ? pluralize(count, noun) : count} selected
      </span>
      {children}
      <Button
        size="sm"
        variant="ghost"
        onClick={onClear}
        aria-label="Clear selection"
      >
        <X className="mr-1 size-3.5" />
        Clear
      </Button>
    </section>
  );
}
