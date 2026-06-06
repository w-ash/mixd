/**
 * Alerts the user when a schedule's recent runs have been failing.
 *
 * Keyed on `consecutive_failures` — the scheduler resets it to 0 on the next
 * successful run, so the banner clears itself without any dismiss state. Shows
 * the sanitized `last_error` only (the backend records a leak-safe label, never
 * raw token text); full detail lives in the linked run history.
 */

import { AlertTriangle } from "lucide-react";

import type { ScheduleResponse } from "#/api/generated/model";

interface ScheduleFailureBannerProps {
  schedule: Pick<ScheduleResponse, "consecutive_failures" | "last_error">;
}

export function ScheduleFailureBanner({
  schedule,
}: ScheduleFailureBannerProps) {
  if (schedule.consecutive_failures < 1) return null;

  const count = schedule.consecutive_failures;
  const runWord = count === 1 ? "run" : "runs";

  return (
    <div
      role="alert"
      className="flex items-start gap-2.5 rounded-md border-l-2 border-secondary bg-secondary/10 px-4 py-3"
    >
      <AlertTriangle className="mt-0.5 size-4 shrink-0 text-secondary" />
      <div className="min-w-0">
        <p className="font-display text-sm text-text">
          {count} consecutive scheduled {runWord} failed
        </p>
        {schedule.last_error && (
          <p className="mt-0.5 truncate font-mono text-xs text-text-muted">
            {schedule.last_error}
          </p>
        )}
      </div>
    </div>
  );
}
