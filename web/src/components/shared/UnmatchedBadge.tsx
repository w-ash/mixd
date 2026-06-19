import { AlertTriangle } from "lucide-react";

import { Badge } from "#/components/ui/badge";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "#/components/ui/tooltip";

interface UnmatchedBadgeProps {
  count: number | null | undefined;
}

/**
 * Gold "N unmatched" chip for canonical tracks that had no match on the
 * destination connector and so could not be pushed. Renders nothing when there
 * are none. Connector-agnostic wording (works for any destination); the tooltip
 * reassures the tracks aren't lost.
 */
export function UnmatchedBadge({ count }: UnmatchedBadgeProps) {
  if (!count || count <= 0) return null;

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        {/* tabIndex makes the informational chip keyboard-focusable so the
            reassurance is reachable without a mouse (WCAG). */}
        <Badge
          tabIndex={0}
          variant="outline"
          className="cursor-default gap-1 border-status-expired/40 text-status-expired"
        >
          <AlertTriangle />
          {count} unmatched
        </Badge>
      </TooltipTrigger>
      <TooltipContent>
        No matching track found on the destination — they stay in your Mixd
        playlist.
      </TooltipContent>
    </Tooltip>
  );
}
