/**
 * Pure helpers for rendering schedule cadence in plain English.
 *
 * The wire uses cron's weekday convention (0 = Sunday … 6 = Saturday) and a
 * 24-hour `hour`/`minute`; these turn that into the friendly strings the
 * SchedulePicker shows ("Weekly on Sunday at 6:30 AM"). No timezone math —
 * `next_run_at` arrives pre-computed in UTC and is formatted separately.
 */

import type { ScheduleResponse } from "#/api/generated/model";
import { formatDateTime } from "#/lib/format";

export const WEEKDAYS = [
  "Sunday",
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
] as const;

/** The browser's IANA timezone, used as the default when creating a schedule. */
export function browserTimezone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
}

/** Format a 24-hour `hour`/`minute` as a 12-hour clock string ("6:30 AM"). */
export function formatClockTime(hour: number, minute: number): string {
  const period = hour < 12 ? "AM" : "PM";
  const h12 = hour % 12 === 0 ? 12 : hour % 12;
  return `${h12}:${String(minute).padStart(2, "0")} ${period}`;
}

/**
 * Plain-English cadence summary, e.g. "Daily at 6:30 AM (America/Los_Angeles)"
 * or "Weekly on Sunday at 6:30 AM (UTC)". Reads `schedule_type`/`day_of_week`
 * straight off the response.
 */
export function describeSchedule(schedule: {
  schedule_type: "daily" | "weekly";
  hour: number;
  minute: number;
  day_of_week: number | null;
  timezone: string;
}): string {
  const at = formatClockTime(schedule.hour, schedule.minute);
  if (schedule.schedule_type === "weekly" && schedule.day_of_week != null) {
    return `Weekly on ${WEEKDAYS[schedule.day_of_week]} at ${at} (${schedule.timezone})`;
  }
  return `Daily at ${at} (${schedule.timezone})`;
}

/**
 * The background-syncable targets the Sync page renders a scheduler for — the
 * minimal frontend mirror of the backend's canonical `SYNC_DISPATCH`. `SyncTarget`
 * types the card literals so a drifted id is a compile error, not a runtime 404.
 * Friendly display names live server-side (`target_label`), not here.
 */
export const SYNC_TARGETS = [
  "lastfm:plays",
  "spotify:likes",
  "lastfm:likes",
] as const;

export type SyncTarget = (typeof SYNC_TARGETS)[number];

/**
 * Whether a schedule's recent runs are actively failing.
 *
 * Gated on `status === "enabled"`: a disabled schedule never runs, so the
 * scheduler can never reset its streak to 0 — counting it as failing would pin
 * the dashboard banner and row marker open forever after the user paused it.
 * The one predicate behind every failure surface (banner, aggregate, row marker).
 */
export function isScheduleFailing(
  schedule: Pick<ScheduleResponse, "status" | "consecutive_failures">,
): boolean {
  return schedule.status === "enabled" && schedule.consecutive_failures >= 1;
}

/** Format `next_run_at` (UTC ISO) in the schedule's own timezone for display. */
export function formatNextRun(schedule: ScheduleResponse): string {
  if (!schedule.next_run_at) return "—";
  try {
    return formatDateTime(schedule.next_run_at, {
      timeZone: schedule.timezone,
    });
  } catch {
    // Unknown timezone (shouldn't happen — validated server-side); fall back.
    return new Date(schedule.next_run_at).toLocaleString();
  }
}
