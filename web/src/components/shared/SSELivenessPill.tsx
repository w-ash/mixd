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
import { formatElapsed } from "#/lib/format";
import { cn } from "#/lib/utils";

const HIDE_BELOW_MS = 10_000;
const AMBER_THRESHOLD_MS = 30_000;
const RED_THRESHOLD_MS = 60_000;

type Tier = "neutral" | "amber" | "red";

const TIER_STYLES: Record<Tier, { pill: string; dot: string }> = {
  neutral: { pill: "bg-surface-sunken text-text-muted", dot: "bg-text-faint" },
  amber: {
    pill: "bg-status-warning/10 text-status-warning",
    dot: "bg-status-warning",
  },
  red: { pill: "bg-status-error/10 text-status-error", dot: "bg-status-error" },
};

function tierFor(elapsedMs: number): Tier {
  if (elapsedMs >= RED_THRESHOLD_MS) return "red";
  if (elapsedMs >= AMBER_THRESHOLD_MS) return "amber";
  return "neutral";
}

interface SSELivenessPillProps {
  className?: string;
}

export function SSELivenessPill({ className }: SSELivenessPillProps) {
  const { sseState, lastEventAt } = useSSELivenessContext();
  const now = useNow(1000);

  if (sseState.kind === "stalled") {
    const elapsed = now - sseState.lastEventAt;
    return (
      <div
        role="alert"
        aria-live="polite"
        className={cn(
          "rounded-md border border-status-warning/40 bg-status-warning/5 px-3 py-2 font-display text-xs text-status-warning",
          className,
        )}
      >
        No update for {formatElapsed(elapsed)}. Checking…
      </div>
    );
  }

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

  const tier = tierFor(elapsed);
  const styles = TIER_STYLES[tier];
  const label =
    tier === "red"
      ? `Connection may be stale — last update ${formatElapsed(elapsed)}`
      : `Last update ${formatElapsed(elapsed)}`;

  return (
    <span
      aria-live="polite"
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 font-display text-[10px] uppercase tracking-wide",
        styles.pill,
        className,
      )}
    >
      <span
        aria-hidden="true"
        className={cn("size-1.5 rounded-full", styles.dot)}
      />
      {label}
    </span>
  );
}
