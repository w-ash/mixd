import { Check, Circle } from "lucide-react";

import { Badge } from "#/components/ui/badge";

type ImportStatus = "not_imported" | "imported";

interface ImportStatusPillProps {
  status: ImportStatus;
}

/**
 * Per-row badge on the Spotify browser dialog. "Not imported" uses the
 * muted outline variant so the user's eye lands on rows they haven't
 * pulled in yet; "Imported" uses the warm primary variant to read as
 * the positive, completed state.
 *
 * Icon + color + text per the web-design-system rule: never color alone.
 */
export function ImportStatusPill({ status }: ImportStatusPillProps) {
  if (status === "imported") {
    return (
      <Badge variant="default" className="gap-1">
        <Check />
        Imported
      </Badge>
    );
  }
  return (
    <Badge variant="outline" className="gap-1 text-text-muted">
      <Circle />
      Not imported
    </Badge>
  );
}
