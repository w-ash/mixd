import { useInfiniteQuery } from "@tanstack/react-query";
import { History } from "lucide-react";
import { useCallback, useMemo } from "react";
import {
  getListPlaysApiV1PlaysGetQueryKey,
  listPlaysApiV1PlaysGet,
  useGetPlaysHistogramApiV1PlaysHistogramGet,
} from "#/api/generated/plays/plays";
import { STALE } from "#/api/query-client";
import { PlayFeed } from "#/components/plays/PlayFeed";
import { PlaysBarChart } from "#/components/plays/PlaysBarChart";
import { DismissibleChip } from "#/components/shared/DismissibleChip";
import { EmptyState } from "#/components/shared/EmptyState";
import { QueryStates } from "#/components/shared/QueryStates";
import { ListRowsSkeleton } from "#/components/shared/skeletons";
import { useFilterState } from "#/hooks/useFilterState";
import { browserTimezone, formatDate } from "#/lib/format";
import { bucketRange, groupPlaysByDay } from "#/lib/play-feed";

const PAGE_SIZE = 100;

interface PlaysHistoryViewProps {
  /** Fix the feed to one track (Track Detail); omitted, the URL drives it. */
  trackId?: string;
  /** Test harness passthrough for the virtualized list. */
  initialItemCount?: number;
  /** Test harness passthrough: jsdom gives ResponsiveContainer zero width. */
  chartWidth?: number;
}

/**
 * Chart-as-navigator over the play feed. One URL-driven filter model
 * (`from` / `to` / `service` / `track_id`) feeds both queries; a bar click
 * narrows the range, the chip clears it.
 */
export function PlaysHistoryView({
  trackId,
  initialItemCount,
  chartWidth,
}: PlaysHistoryViewProps) {
  const { searchParams, setFilters } = useFilterState();
  const from = searchParams.get("from");
  const to = searchParams.get("to");
  const service = searchParams.get("service");
  const effectiveTrackId = trackId ?? searchParams.get("track_id");

  const filterParams = useMemo(
    () => ({
      since: from ?? undefined,
      until: to ?? undefined,
      service: service ?? undefined,
      track_id: effectiveTrackId ?? undefined,
    }),
    [from, to, service, effectiveTrackId],
  );

  const histogram = useGetPlaysHistogramApiV1PlaysHistogramGet(
    {
      ...filterParams,
      tz: browserTimezone(),
    },
    { query: { staleTime: STALE.FAST } },
  );
  const histogramData =
    histogram.data?.status === 200 ? histogram.data.data : undefined;

  const feed = useInfiniteQuery({
    queryKey: [...getListPlaysApiV1PlaysGetQueryKey(filterParams), "infinite"],
    queryFn: ({ pageParam }) =>
      listPlaysApiV1PlaysGet({
        ...filterParams,
        limit: PAGE_SIZE,
        cursor: pageParam ?? undefined,
      }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) =>
      last.status === 200 ? last.data.next_cursor : null,
    staleTime: STALE.FAST,
  });

  const events = useMemo(
    () =>
      (feed.data?.pages ?? []).flatMap((page) =>
        page.status === 200 ? page.data.data : [],
      ),
    [feed.data],
  );
  const groups = useMemo(() => groupPlaysByDay(events), [events]);

  const handleBarClick = useCallback(
    (binStart: string) => {
      if (!histogramData) return;
      const range = bucketRange(binStart, histogramData.bucket);
      setFilters({ from: range.from, to: range.to });
    },
    [histogramData, setFilters],
  );

  const clearRange = useCallback(() => {
    setFilters({ from: null, to: null });
  }, [setFilters]);

  const fetchNext = useCallback(() => {
    if (feed.hasNextPage && !feed.isFetchingNextPage) {
      void feed.fetchNextPage();
    }
  }, [feed]);

  return (
    <div className="space-y-4">
      {histogramData && histogramData.bins.length > 0 && (
        <PlaysBarChart
          bins={histogramData.bins}
          bucket={histogramData.bucket}
          onBarClick={handleBarClick}
          width={chartWidth}
        />
      )}

      {from && to && (
        <div className="flex items-center gap-2 text-xs">
          <DismissibleChip
            label={`${formatDate(from)} – ${formatDate(to)}`}
            onRemove={clearRange}
            ariaRemoveLabel="Clear date range"
          />
        </div>
      )}

      <QueryStates
        loading={feed.isLoading}
        isError={feed.isError}
        error={feed.error}
        errorHeading="Couldn't load plays"
        skeleton={
          <ListRowsSkeleton rows={8} bars={["h-4 w-56", "ml-auto h-3 w-16"]} />
        }
        isEmpty={events.length === 0}
        empty={
          <EmptyState
            icon={<History className="size-8" aria-hidden="true" />}
            heading="No plays yet"
            description={
              from && to
                ? "No plays in this period. Clear the range to see everything."
                : "Import listening history or connect a service to start the feed."
            }
          />
        }
      >
        <PlayFeed
          groups={groups}
          onEndReached={fetchNext}
          initialItemCount={initialItemCount}
        />
        {feed.isFetchingNextPage && (
          <p className="py-2 text-center font-mono text-xs text-text-muted">
            Loading more…
          </p>
        )}
      </QueryStates>
    </div>
  );
}
