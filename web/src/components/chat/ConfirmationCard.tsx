import { AlertTriangle, Check, Loader2, X } from "lucide-react";
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

/** Narrow an unknown detail value to `string[]` (the before/after lines). */
function asChanges(value: unknown): string[] | undefined {
  if (Array.isArray(value) && value.every((item) => typeof item === "string")) {
    return value as string[];
  }
  return undefined;
}

/** Narrow an unknown detail value to a non-empty string. */
function asString(value: unknown): string | undefined {
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

/**
 * Approval gate for a mutating tool (create/update/delete a playlist, tag, or
 * workflow). Write tools return a STANDARD `details` shape — `changes` (a
 * before/after line list), an optional `severity`, and an optional `warning` —
 * so the confirmation is a real decision, never a rubber stamp. Any `warning`
 * surfaces in a banner BEFORE the Confirm button (so vague approvals never train
 * users to click yes); `severity: "destructive"` escalates its styling to the
 * red alert treatment, while a soft warning gets a muted banner.
 * Tools without the standard shape fall back to the generic key/value display.
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

  const changes = asChanges(details.changes);
  const isDestructive = asString(details.severity) === "destructive";
  const warning = asString(details.warning);

  return (
    <div
      className={cn(
        "rounded-lg border-l-2 bg-surface px-4 py-3",
        isDestructive ? "border-destructive" : "border-primary",
        isResolved && "opacity-75",
      )}
    >
      <p className="mb-2 font-display text-xs font-medium text-text">
        {description}
      </p>

      {changes && changes.length > 0 ? (
        <ul className="space-y-1 text-xs text-text">
          {changes.map((line, index) => (
            <li
              // biome-ignore lint/suspicious/noArrayIndexKey: changes are static render-once lines that may repeat
              key={index}
              className="flex gap-2"
            >
              <span
                aria-hidden="true"
                className="mt-1.5 size-1 shrink-0 rounded-full bg-text-faint"
              />
              <span className="font-body">{line}</span>
            </li>
          ))}
        </ul>
      ) : (
        <GenericToolResultCard result={details} />
      )}

      {warning && (
        <div
          role="alert"
          className={cn(
            "mt-2.5 flex items-start gap-2.5 rounded-md border-l-2 px-3 py-2",
            isDestructive
              ? "border-destructive bg-destructive/10 text-destructive"
              : "border-text-faint bg-text-faint/10 text-text-muted",
          )}
        >
          <AlertTriangle className="mt-0.5 size-4 shrink-0" />
          <p className="font-display text-xs font-medium">{warning}</p>
        </div>
      )}

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
