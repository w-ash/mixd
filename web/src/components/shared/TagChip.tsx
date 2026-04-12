import { X } from "lucide-react";

import { Badge } from "#/components/ui/badge";
import { cn } from "#/lib/utils";

interface TagChipProps {
  tag: string;
  onRemove?: () => void;
  className?: string;
}

export function TagChip({ tag, onRemove, className }: TagChipProps) {
  return (
    <Badge variant="outline" className={cn("gap-1.5 font-mono", className)}>
      <span>{tag}</span>
      {onRemove && (
        <button
          type="button"
          onClick={onRemove}
          aria-label={`Remove ${tag}`}
          className="-mr-1 rounded-sm text-text-muted transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
        >
          <X className="size-3" strokeWidth={2.5} />
        </button>
      )}
    </Badge>
  );
}
