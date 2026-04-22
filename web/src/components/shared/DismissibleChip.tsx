import { X } from "lucide-react";

import { Badge } from "#/components/ui/badge";
import { cn } from "#/lib/utils";

export type DismissibleChipFontVariant = "mono" | "display";

interface DismissibleChipProps {
  label: string;
  /** `"mono"` for user-authored tokens (tags, IDs); `"display"` (default) for
   * system-authored labels (filter facets, active-filter summaries). */
  fontVariant?: DismissibleChipFontVariant;
  /** When provided, a close button is rendered. Omit for read-only badges. */
  onRemove?: () => void;
  /** Accessible label override for the remove button. Defaults to `Remove ${label}`. */
  ariaRemoveLabel?: string;
  className?: string;
}

/**
 * Outline badge + optional dismiss button. Unifies the tag-chip and
 * filter-chip patterns: both render the same DOM, differing only in font
 * (mono for user-authored tokens, display for system-labeled facets).
 */
export function DismissibleChip({
  label,
  fontVariant = "display",
  onRemove,
  ariaRemoveLabel,
  className,
}: DismissibleChipProps) {
  return (
    <Badge
      variant="outline"
      className={cn(
        "gap-1.5",
        fontVariant === "mono" && "font-mono",
        className,
      )}
    >
      <span>{label}</span>
      {onRemove && (
        <button
          type="button"
          onClick={onRemove}
          aria-label={ariaRemoveLabel ?? `Remove ${label}`}
          className="-mr-1 rounded-sm text-text-muted transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
        >
          <X className="size-3" strokeWidth={2.5} />
        </button>
      )}
    </Badge>
  );
}
