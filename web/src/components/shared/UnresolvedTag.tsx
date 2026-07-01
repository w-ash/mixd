import { AlertTriangle } from "lucide-react";

/**
 * Inline "Unresolved" indicator for a playlist entry whose connector track has
 * no canonical match yet (the position is preserved; the track id is null).
 * Distinct from `UnmatchedBadge`, which is about push-to-connector misses.
 */
export function UnresolvedTag() {
  return (
    <span className="inline-flex items-center gap-1 text-xs text-status-expired">
      <AlertTriangle className="size-3 shrink-0" aria-hidden="true" />
      Unresolved
    </span>
  );
}
