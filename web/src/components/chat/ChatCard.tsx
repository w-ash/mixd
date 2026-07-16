import type { ReactNode } from "react";

import { cn } from "#/lib/utils";

/**
 * Shared accent-shell for chat result cards — the left-accent-bar treatment
 * (`rounded-lg border-l-2 border-primary bg-surface px-4 py-3`) that
 * ConfirmationCard, OperationProgressCard, and WorkflowPreviewCard all share.
 *
 * `variant` maps the accent-bar color (primary vs destructive). `dimmed`
 * applies the resolved-state opacity. `header`, when given, is rendered above
 * the children as the card's title row (typically a
 * `font-display text-xs font-medium text-text` line, or a flex row pairing that
 * title with a trailing count/link).
 *
 * NOT used by CodeExecutionCard: that card is a deliberate non-accent
 * `border border-border` trace treatment (a sandbox transcript, not a
 * mixd-domain result), so it stays outside this shell by design.
 */
export function ChatCard({
  variant = "primary",
  dimmed = false,
  header,
  className,
  children,
}: {
  variant?: "primary" | "destructive";
  dimmed?: boolean;
  header?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div
      className={cn(
        "rounded-lg border-l-2 bg-surface px-4 py-3",
        variant === "destructive" ? "border-destructive" : "border-primary",
        dimmed && "opacity-75",
        className,
      )}
    >
      {header}
      {children}
    </div>
  );
}
