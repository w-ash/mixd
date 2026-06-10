/**
 * The caller's schedules whose recent runs are failing.
 *
 * Single source of "what's automated and currently broken", read off the same
 * caller-scoped `GET /schedules` list the workflow page already uses. Uses the
 * shared `isScheduleFailing` predicate (enabled + non-zero streak), so a paused
 * schedule drops out and a recovered one self-clears. Feeds both the dashboard
 * aggregate banner and the workflow-list failing marker.
 */

import type { ScheduleListItem } from "#/api/generated/model";
import { useListSchedulesApiV1SchedulesGet } from "#/api/generated/schedules/schedules";
import { STALE } from "#/api/query-client";
import { isScheduleFailing } from "#/lib/schedule";

export interface ScheduleFailures {
  failing: ScheduleListItem[];
  count: number;
}

export function useScheduleFailures(): ScheduleFailures {
  const { data } = useListSchedulesApiV1SchedulesGet({
    query: { staleTime: STALE.SLOW },
  });
  const rows = data?.status === 200 ? data.data.data : [];
  const failing = rows.filter(isScheduleFailing);
  return { failing, count: failing.length };
}
