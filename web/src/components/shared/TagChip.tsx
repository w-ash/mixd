import { DismissibleChip } from "./DismissibleChip";

interface TagChipProps {
  tag: string;
  onRemove?: () => void;
  className?: string;
}

/**
 * User-authored tag pill. Thin wrapper over DismissibleChip with
 * `fontVariant="mono"` — the tag string is a normalized identifier, so
 * monospace distinguishes it from system-authored filter labels.
 */
export function TagChip({ tag, onRemove, className }: TagChipProps) {
  return (
    <DismissibleChip
      label={tag}
      fontVariant="mono"
      onRemove={onRemove}
      className={className}
    />
  );
}
