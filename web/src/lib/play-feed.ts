import type { PlayEventSchema } from "#/api/generated/model";

/** A run of consecutive plays of one track, rendered as a single feed row. */
export interface CollapsedPlayRow {
  key: string;
  trackId: string;
  title: string;
  artists: string;
  service: string;
  sourceServices: string[];
  count: number;
  /** Newest play in the run (the feed is newest-first). */
  newestAt: string;
  /** Oldest play in the run. */
  oldestAt: string;
}

export interface PlayDayGroup {
  /** Local calendar day, YYYY-MM-DD. */
  dayKey: string;
  /** "Today", "Yesterday", or an absolute date. */
  label: string;
  rows: CollapsedPlayRow[];
}

function localDayKey(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export function formatDayLabel(dayKey: string, now: Date = new Date()): string {
  const todayKey = localDayKey(now);
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  const yesterdayKey = localDayKey(yesterday);
  if (dayKey === todayKey) return "Today";
  if (dayKey === yesterdayKey) return "Yesterday";
  const [y, m, d] = dayKey.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    year: y === now.getFullYear() ? undefined : "numeric",
  });
}

/**
 * Group newest-first play events into local-day groups, collapsing runs of
 * consecutive plays of the same track into one row with a count.
 */
export function groupPlaysByDay(
  events: PlayEventSchema[],
  now: Date = new Date(),
): PlayDayGroup[] {
  const groups: PlayDayGroup[] = [];
  for (const event of events) {
    const dayKey = localDayKey(new Date(event.played_at));
    let group = groups.at(-1);
    if (!group || group.dayKey !== dayKey) {
      group = { dayKey, label: formatDayLabel(dayKey, now), rows: [] };
      groups.push(group);
    }
    const prev = group.rows.at(-1);
    if (prev && prev.trackId === event.track_id) {
      prev.count += 1;
      prev.oldestAt = event.played_at;
    } else {
      group.rows.push({
        key: event.id,
        trackId: event.track_id,
        title: event.title,
        artists: event.artists,
        service: event.service,
        sourceServices: event.source_services,
        count: 1,
        newestAt: event.played_at,
        oldestAt: event.played_at,
      });
    }
  }
  return groups;
}

/** Date range covered by one histogram bin — feeds the chart's click-to-filter. */
export function bucketRange(
  binStart: string,
  bucket: "day" | "week" | "month",
): { from: string; to: string } {
  const start = new Date(binStart);
  const end = new Date(start);
  if (bucket === "day") end.setDate(start.getDate() + 1);
  else if (bucket === "week") end.setDate(start.getDate() + 7);
  else end.setMonth(start.getMonth() + 1);
  return { from: start.toISOString(), to: end.toISOString() };
}
