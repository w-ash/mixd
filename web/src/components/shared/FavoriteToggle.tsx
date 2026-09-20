import { Heart } from "lucide-react";

import { Button } from "#/components/ui/button";
import { cn } from "#/lib/utils";

export interface FavoriteToggleProps {
  isFavorited: boolean;
  onToggle: () => void;
  disabled?: boolean;
  /** `sm` for list rows, `md` for detail headers. */
  size?: "sm" | "md";
  /** Name of the favorited entity — completes the accessible label. */
  label?: string;
}

/**
 * Heart toggle for favoriting an entity. One component wherever a favorite is
 * set, so the icon, colour, and label wording match across list rows and
 * detail headers.
 *
 * The click stops propagating: the heart sits inside rows that navigate on
 * click, and favoriting must never also open the row.
 */
export function FavoriteToggle({
  isFavorited,
  onToggle,
  disabled = false,
  size = "md",
  label,
}: FavoriteToggleProps) {
  const action = isFavorited ? "Unfavorite" : "Favorite";
  const accessibleLabel = label ? `${action} ${label}` : action;

  return (
    <Button
      type="button"
      variant="ghost"
      size={size === "sm" ? "icon-sm" : "icon"}
      aria-pressed={isFavorited}
      aria-label={accessibleLabel}
      title={accessibleLabel}
      disabled={disabled}
      onClick={(event) => {
        event.stopPropagation();
        onToggle();
      }}
      className={cn(
        "transition-colors",
        isFavorited
          ? "text-status-liked hover:text-status-liked"
          : "text-text-faint hover:text-text",
      )}
    >
      <Heart
        className={cn(
          size === "sm" ? "size-3.5" : "size-5",
          isFavorited && "fill-current",
        )}
        aria-hidden="true"
      />
    </Button>
  );
}
