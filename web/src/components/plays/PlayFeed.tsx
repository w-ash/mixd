import { Link } from "react-router";
import { GroupedVirtuoso } from "react-virtuoso";
import { ConnectorIcon } from "#/components/shared/ConnectorIcon";
import { formatRelativeTime, formatTimeOfDay } from "#/lib/format";
import type { CollapsedPlayRow, PlayDayGroup } from "#/lib/play-feed";

interface PlayFeedProps {
  groups: PlayDayGroup[];
  /** Fires when the list scrolls near its end — load the next page. */
  onEndReached?: () => void;
  /** Test harness: pre-render N rows (jsdom cannot measure). */
  initialItemCount?: number;
  className?: string;
}

function PlayRow({
  row,
  isToday,
}: {
  row: CollapsedPlayRow;
  isToday: boolean;
}) {
  const exact = new Date(row.newestAt).toLocaleString();
  const timeLabel = isToday
    ? formatRelativeTime(row.newestAt)
    : formatTimeOfDay(row.newestAt);
  return (
    <div className="flex items-center gap-3 border-b border-border-muted px-4 py-3">
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2">
          <Link
            to={`/library/${row.trackId}`}
            viewTransition
            className="truncate font-medium text-text transition-colors hover:text-primary"
          >
            {row.title}
          </Link>
          {row.count > 1 && (
            <span
              className="shrink-0 font-mono text-xs text-primary"
              title={`Played ${row.count} times in a row`}
            >
              ×{row.count}
            </span>
          )}
        </div>
        <p className="truncate text-sm text-text-muted">{row.artists}</p>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {row.sourceServices.map((name) => (
          <ConnectorIcon key={name} name={name} labelHidden />
        ))}
        <span
          className="font-mono text-xs tabular-nums text-text-muted"
          title={exact}
        >
          {row.count > 1
            ? `${formatTimeOfDay(row.oldestAt)}–${formatTimeOfDay(row.newestAt)}`
            : timeLabel}
        </span>
      </div>
    </div>
  );
}

/**
 * Virtualized play feed: sticky local-day headers, consecutive same-track
 * runs collapsed to one ×N row. Rows arrive pre-grouped via
 * ``groupPlaysByDay``.
 */
export function PlayFeed({
  groups,
  onEndReached,
  initialItemCount,
  className,
}: PlayFeedProps) {
  const rows = groups.flatMap((g) => g.rows);
  const todayLabelSet = new Set(
    groups.filter((g) => g.label === "Today").map((g) => g.dayKey),
  );
  const dayKeyByRowIndex: string[] = groups.flatMap((g) =>
    g.rows.map(() => g.dayKey),
  );

  return (
    <div className={className ?? "h-[65vh]"} data-testid="play-feed">
      <GroupedVirtuoso
        groupCounts={groups.map((g) => g.rows.length)}
        initialItemCount={initialItemCount}
        groupContent={(index) => (
          <div className="border-b border-border bg-surface px-4 py-2 font-display text-xs uppercase tracking-wider text-text-muted">
            {groups[index].label}
          </div>
        )}
        itemContent={(index) => (
          <PlayRow
            row={rows[index]}
            isToday={todayLabelSet.has(dayKeyByRowIndex[index])}
          />
        )}
        endReached={onEndReached}
      />
    </div>
  );
}
