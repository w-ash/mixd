import { ArrowRight } from "lucide-react";
import { Link } from "react-router";
import {
  useGetPlaysHistogramApiV1PlaysHistogramGet,
  useListPlaysApiV1PlaysGet,
} from "#/api/generated/plays/plays";
import { STALE } from "#/api/query-client";
import { PlaysBarChart } from "#/components/plays/PlaysBarChart";
import { ConnectorIcon } from "#/components/shared/ConnectorIcon";
import {
  browserTimezone,
  formatDateTime,
  formatRelativeTime,
} from "#/lib/format";

const RECENT_LIMIT = 10;
// Below this a chart reads as noise — the stat line above carries the story.
const MIN_CHART_PLAYS = 5;

interface TrackPlaysSectionProps {
  trackId: string;
  totalPlays: number;
}

/**
 * Compact play history for Track Detail: binned activity chart, the most
 * recent plays, and a link into the full feed pre-filtered to this track.
 */
export function TrackPlaysSection({
  trackId,
  totalPlays,
}: TrackPlaysSectionProps) {
  const showChart = totalPlays >= MIN_CHART_PLAYS;
  const histogram = useGetPlaysHistogramApiV1PlaysHistogramGet(
    {
      track_id: trackId,
      tz: browserTimezone(),
    },
    { query: { staleTime: STALE.FAST, enabled: showChart } },
  );
  const recent = useListPlaysApiV1PlaysGet(
    { track_id: trackId, limit: RECENT_LIMIT },
    { query: { staleTime: STALE.FAST } },
  );

  const histogramData =
    histogram.data?.status === 200 ? histogram.data.data : undefined;
  const events = recent.data?.status === 200 ? recent.data.data.data : [];

  return (
    <div className="mt-4 space-y-4">
      {showChart && histogramData && histogramData.bins.length > 0 && (
        <PlaysBarChart
          bins={histogramData.bins}
          bucket={histogramData.bucket}
        />
      )}

      {events.length > 0 && (
        <ul className="space-y-2" aria-label="Recent plays">
          {events.map((event) => (
            <li
              key={event.id}
              className="flex items-center justify-between gap-2 text-sm"
            >
              <span
                className="text-text-muted"
                title={formatDateTime(event.played_at)}
              >
                {formatRelativeTime(event.played_at)}
              </span>
              <span className="flex shrink-0 gap-1">
                {event.source_services.map((service) => (
                  <ConnectorIcon key={service} name={service} labelHidden />
                ))}
              </span>
            </li>
          ))}
        </ul>
      )}

      <Link
        to={`/library/plays?track_id=${trackId}`}
        className="inline-flex items-center gap-1 text-sm text-text-muted transition-colors hover:text-primary"
      >
        View all plays
        <ArrowRight className="size-3.5" aria-hidden="true" />
      </Link>
    </div>
  );
}
