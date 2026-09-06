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
import {
  type listSchedulesApiV1SchedulesGetResponse,
  useListSchedulesApiV1SchedulesGet,
} from "#/api/generated/schedules/schedules";
import { STALE } from "#/api/query-client";
import { isScheduleFailing } from "#/lib/schedule";

export interface ScheduleFailures {
  failing: ScheduleListItem[];
  count: number;
}

/** Stable empty array so consumers don't see a new reference every render. */
const NONE: ScheduleListItem[] = [];

/**
 * Module-level so its identity is stable across renders: Tanstack memoises
 * `select` on `(data, selectFn)`, so an inline arrow would re-filter and mint a
 * fresh array on every render of every consumer.
 */
function selectFailing(
  res: listSchedulesApiV1SchedulesGetResponse,
): ScheduleListItem[] {
  return res.status === 200 ? res.data.data.filter(isScheduleFailing) : NONE;
}

export function useScheduleFailures(): ScheduleFailures {
  const { data } = useListSchedulesApiV1SchedulesGet({
    query: { staleTime: STALE.SLOW, select: selectFailing },
  });
  const failing = data ?? NONE;
  return { failing, count: failing.length };
}
