import type { ReactNode } from "react";

import { ConnectorIcon } from "@/components/shared/ConnectorIcon";
import { cn } from "@/lib/utils";

interface ConnectorListItemProps {
  connectorName: string;
  children: ReactNode;
  actions?: ReactNode;
  /** Dim the item (e.g. non-primary mappings). Default false. */
  muted?: boolean;
}

/**
 * Shared layout for connector-linked items (track mappings, playlist links).
 *
 * Structure: [ConnectorIcon] [children content] [actions]
 * Consistent card container, padding, border, and hover treatment.
 */
export function ConnectorListItem({
  connectorName,
  children,
  actions,
  muted = false,
}: ConnectorListItemProps) {
  return (
    <div
      className={cn(
        "group flex items-center gap-3 rounded-md border-l-2 bg-surface-inset px-4 py-3 transition-colors hover:border-primary/40",
        muted ? "border-border/50 opacity-75" : "border-border",
      )}
    >
      <ConnectorIcon name={connectorName} />
      <div className="min-w-0 flex-1">{children}</div>
      {actions && <div className="flex items-center gap-1">{actions}</div>}
    </div>
  );
}
