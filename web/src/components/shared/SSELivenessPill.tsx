/**
 * Liveness indicator for an in-flight SSE-driven workflow run.
 *
 * Two visual modes:
 *   - Pill: "Last update Xs ago" — appears once a frame has been
 *     received and at least 10s have passed without a new one. Goes
 *     amber at 30s, red at 60s. Hidden during the very fresh window
 *     so fast (<10s) workflows never trigger it.
 *   - Banner: "No update for Xs. Checking…" — appears when the
 *     watchdog (45s) has fired and SSE state is "stalled". Has
 *     role="alert" + aria-live="polite" for screen readers.
 *
 * Reads from SSELivenessContext only — does not subscribe to
 * nodeStatuses or any execution-state, so it ticks once per second
 * without re-rendering domain consumers.
 */

import { useSSELivenessContext } from "#/contexts/WorkflowExecutionContext";
import { useNow } from "#/hooks/useNow";
import { cn } from "#/lib/utils";

const HIDE_BELOW_MS = 10_000;
const AMBER_THRESHOLD_MS = 30_000;
const RED_THRESHOLD_MS = 60_000;

function formatRelative(ms: number): string {
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  return `${Math.floor(m / 60)}h ago`;
}

interface SSELivenessPillProps {
  className?: string;
}

export function SSELivenessPill({ className }: SSELivenessPillProps) {
  const { sseState, lastEventAt } = useSSELivenessContext();
  const now = useNow(1000);

  // Render the stall banner whenever the watchdog has tripped.
  if (sseState.kind === "stalled") {
    const elapsed = now - sseState.lastEventAt;
    return (
      <div
        role="alert"
        aria-live="polite"
        className={cn(
          "rounded-md border border-amber-500/40 bg-amber-500/5 px-3 py-2 font-display text-xs text-amber-300",
          className,
        )}
      >
        No update for {formatRelative(elapsed)}. Checking…
      </div>
    );
  }

  // No pill if we don't have a freshness signal yet, or if the
  // connection is in a transient state where the pill would be noise.
  if (lastEventAt === null) return null;
  if (
    sseState.kind === "idle" ||
    sseState.kind === "connecting" ||
    sseState.kind === "open-no-events" ||
    sseState.kind === "closed-done"
  ) {
    return null;
  }

  const elapsed = now - lastEventAt;
  if (elapsed < HIDE_BELOW_MS) return null;

  const isRed = elapsed >= RED_THRESHOLD_MS;
  const isAmber = !isRed && elapsed >= AMBER_THRESHOLD_MS;

  return (
    <span
      aria-live="polite"
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 font-display text-[10px] uppercase tracking-wide",
        isRed && "bg-red-500/10 text-red-300",
        isAmber && "bg-amber-500/10 text-amber-300",
        !isRed && !isAmber && "bg-surface-sunken text-text-muted",
        className,
      )}
    >
      <span
        aria-hidden="true"
        className={cn(
          "size-1.5 rounded-full",
          isRed && "bg-red-400",
          isAmber && "bg-amber-400",
          !isRed && !isAmber && "bg-text-faint",
        )}
      />
      {isRed
        ? `Connection may be stale — last update ${formatRelative(elapsed)}`
        : `Last update ${formatRelative(elapsed)}`}
    </span>
  );
}
