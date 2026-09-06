import { formatCount } from "#/lib/format";
import { cn } from "#/lib/utils";

interface PlaylistMetaProps {
  /** Connector-reported owner; "Unknown" when the connector omits it. */
  owner: string | null | undefined;
  trackCount: number;
  className?: string;
}

/**
 * Secondary line of a connector playlist row: owner and track count.
 *
 * Renders as an inline element so it is valid inside both the `<button>` of
 * select mode and the `<label>` of import mode.
 */
export function PlaylistMeta({
  owner,
  trackCount,
  className,
}: PlaylistMetaProps) {
  return (
    <span className={cn("block truncate text-xs text-text-muted", className)}>
      {owner ?? "Unknown"} ·{" "}
      <span className="tabular-nums">{formatCount(trackCount)}</span> tracks
    </span>
  );
}
