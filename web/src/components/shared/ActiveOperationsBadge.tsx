import { Loader2 } from "lucide-react";
import { NavLink } from "react-router";

import { useActiveOperations } from "#/hooks/useActiveOperations";
import { pluralize } from "#/lib/pluralize";
import { cn } from "#/lib/utils";

/**
 * "N operations running" affordance — absence = healthy, so it renders nothing
 * at zero and links to the operation-runs surface when something is in flight.
 *
 * Reads `useActiveOperations()` directly (shared cache) rather than the
 * operations provider — it only needs the count, and stays decoupled from
 * re-attach state.
 */
export function ActiveOperationsBadge({ className }: { className?: string }) {
  const { data: operations = [] } = useActiveOperations();
  const count = operations.length;

  if (count === 0) return null;

  return (
    <NavLink
      to="/settings/imports"
      viewTransition
      className={cn(
        "flex items-center gap-2 rounded-md border-l-2 border-primary bg-surface-elevated px-3 py-2 font-display text-xs text-text-muted transition-colors hover:text-text",
        className,
      )}
    >
      <Loader2
        className="size-3.5 shrink-0 animate-spin text-primary"
        aria-hidden="true"
      />
      <span>{pluralize(count, "operation")} running</span>
    </NavLink>
  );
}
