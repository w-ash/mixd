/**
 * Alerts the user when a schedule's recent runs have been failing.
 *
 * Uses the shared `isScheduleFailing` predicate: shows only for an enabled
 * schedule with a non-zero streak (a paused one isn't actively failing), and the
 * scheduler resets the streak to 0 on the next success, so the banner clears
 * itself without any dismiss state. Shows the sanitized `last_error` only (the
 * backend records a leak-safe label, never raw token text); full detail lives in
 * the linked run history.
 */

import type { ScheduleResponse } from "#/api/generated/model";
import { AlertBanner } from "#/components/shared/AlertBanner";
import { isScheduleFailing } from "#/lib/schedule";

interface ScheduleFailureBannerProps {
  schedule: Pick<
    ScheduleResponse,
    "status" | "consecutive_failures" | "last_error"
  >;
}

export function ScheduleFailureBanner({
  schedule,
}: ScheduleFailureBannerProps) {
  if (!isScheduleFailing(schedule)) return null;

  const count = schedule.consecutive_failures;
  const runWord = count === 1 ? "run" : "runs";

  return (
    <AlertBanner
      title={`${count} consecutive scheduled ${runWord} failed`}
      detail={schedule.last_error ?? undefined}
    />
  );
}
