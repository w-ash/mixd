import type { ReactNode } from "react";

import { ConnectorIcon } from "#/components/shared/ConnectorIcon";
import { cn } from "#/lib/utils";

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
        "group flex flex-col gap-2 rounded-md border-l-2 bg-surface-inset px-4 py-3 transition-colors hover:border-primary/40 lg:flex-row lg:items-center lg:gap-3",
        muted ? "border-border/50 opacity-75" : "border-border",
      )}
    >
      <div className="flex min-w-0 flex-1 items-center gap-3">
        <ConnectorIcon name={connectorName} />
        <div className="min-w-0 flex-1">{children}</div>
      </div>
      {actions && (
        // Mobile: actions drop onto their own line, indented under the content
        // (past the icon); desktop: pinned to the right of the row.
        <div className="flex flex-wrap items-center gap-1 pl-9 lg:pl-0">
          {actions}
        </div>
      )}
    </div>
  );
}
