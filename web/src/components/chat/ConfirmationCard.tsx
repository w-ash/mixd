import { Check, Loader2, X } from "lucide-react";
import { GenericToolResultCard } from "#/components/chat/ToolResultCard";
import { Button } from "#/components/ui/button";
import { cn } from "#/lib/utils";
import type { ConfirmationState } from "#/stores/chat-store";

interface ConfirmationCardProps {
  actionId: string;
  description: string;
  details: Record<string, unknown>;
  toolName: string;
  state: ConfirmationState;
  onConfirm: (actionId: string) => void;
  onCancel: (actionId: string) => void;
}

/**
 * Approval gate for a mutating tool (create/update a playlist or workflow).
 * Concrete before/after details render for every mutation — bespoke card or
 * not — so the confirmation is a real decision, never a rubber stamp. Bespoke
 * per-tool detail renderers land in a later phase; Phase 0 ships the generic
 * key/value display for any proposal shape.
 */
export function ConfirmationCard({
  actionId,
  description,
  details,
  state,
  onConfirm,
  onCancel,
}: ConfirmationCardProps) {
  const isPending = state === "pending";
  const isLoading = state === "loading";
  const isResolved = state === "confirmed" || state === "cancelled";

  return (
    <div
      className={cn(
        "rounded-lg border-l-2 border-primary bg-surface px-4 py-3",
        isResolved && "opacity-75",
      )}
    >
      <p className="mb-2 font-display text-xs font-medium text-text">
        {description}
      </p>

      <GenericToolResultCard result={details} />

      <div className="mt-3 flex items-center gap-2">
        {isResolved ? (
          <span
            className={cn(
              "inline-flex items-center gap-1 font-display text-xs font-medium",
              state === "confirmed" ? "text-status-success" : "text-text-muted",
            )}
          >
            {state === "confirmed" ? (
              <>
                <Check className="size-3.5" />
                Confirmed
              </>
            ) : (
              <>
                <X className="size-3.5" />
                Cancelled
              </>
            )}
          </span>
        ) : (
          <>
            <Button
              variant="default"
              size="sm"
              disabled={!isPending}
              onClick={() => onConfirm(actionId)}
            >
              {isLoading && <Loader2 className="size-3.5 animate-spin" />}
              {isLoading ? "Confirming…" : "Confirm"}
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={isLoading || !isPending}
              onClick={() => onCancel(actionId)}
            >
              Cancel
            </Button>
          </>
        )}
      </div>
    </div>
  );
}
