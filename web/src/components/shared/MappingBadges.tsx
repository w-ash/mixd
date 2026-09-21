/**
 * The badge pair every connector-mapping row carries: which mapping is the
 * primary one, and how it was matched. Shared by the track and artist detail
 * pages so a mapping reads identically wherever it is listed.
 */

import { Badge } from "#/components/ui/badge";
import { matchMethodDescription, matchMethodLabel } from "#/lib/match-methods";

/** Badge sizing for the dense metadata row under a mapping's title. */
export const SMALL_BADGE = "text-[10px] px-1.5 py-0";

export function PrimaryBadge() {
  return (
    <Badge variant="default" className={SMALL_BADGE}>
      Primary
    </Badge>
  );
}

/** How the mapping was matched, with the explanation on hover. */
export function MatchMethodBadge({ method }: { method: string }) {
  return (
    <Badge
      variant="outline"
      className={SMALL_BADGE}
      title={matchMethodDescription(method)}
    >
      {matchMethodLabel(method)}
    </Badge>
  );
}
