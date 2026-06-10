/**
 * Dashboard-level aggregate of failing schedules — the proactive surface so a
 * dead overnight sync gets noticed without opening each schedule. Renders nothing
 * when all schedules are healthy, and self-clears once the scheduler resets the
 * failure streak on a successful run. Per-target detail lives on the sync card
 * and the workflow row; this is just the heads-up.
 */

import { Fragment } from "react";
import { Link } from "react-router";

import type { ScheduleListItem } from "#/api/generated/model";
import { AlertBanner } from "#/components/shared/AlertBanner";
import { useScheduleFailures } from "#/hooks/useScheduleFailures";
import { formatRelativeTime } from "#/lib/format";

/** Where a failing schedule's detail lives — its workflow, or the sync settings. */
function targetHref(schedule: ScheduleListItem): string {
  return schedule.target_type === "workflow" && schedule.workflow_id
    ? `/workflows/${schedule.workflow_id}`
    : "/settings/sync";
}

export function ScheduleFailuresBanner() {
  const { failing, count } = useScheduleFailures();
  if (count === 0) return null;

  const runWord = count === 1 ? "run" : "runs";
  const detail = failing.map((s, i) => (
    <Fragment key={s.id}>
      {i > 0 && " · "}
      <Link to={targetHref(s)} className="underline hover:text-text">
        {s.target_label}
      </Link>{" "}
      ({formatRelativeTime(s.last_run_at)})
    </Fragment>
  ));

  return (
    <AlertBanner
      title={`${count} scheduled ${runWord} failing`}
      detail={detail}
      // The detail is a list of per-target links; let it wrap so every failing
      // target stays visible and clickable instead of being clipped to one line.
      truncateDetail={false}
    />
  );
}
