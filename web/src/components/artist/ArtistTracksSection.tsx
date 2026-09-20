import { Music } from "lucide-react";
import { useEffect, useRef } from "react";
import { useSearchParams } from "react-router";

import { useListTracksApiV1TracksGet } from "#/api/generated/tracks/tracks";
import { TrackTable } from "#/components/library/TrackTable";
import { EmptyState } from "#/components/shared/EmptyState";
import { QueryStates } from "#/components/shared/QueryStates";
import { ListRowsSkeleton } from "#/components/shared/skeletons";
import { TablePagination } from "#/components/shared/TablePagination";
import { usePagination } from "#/hooks/usePagination";

const PAGE_SIZE = 25;

/**
 * Every library track credited to one artist, in the Library's own table.
 *
 * Owns its query and its page position so the artist page above it stays a
 * single-resource detail view. Paging follows the Library: `?page=` in the URL
 * drives the offset, and each response's `next_cursor` is cached so sequential
 * navigation is keyset rather than a deepening offset scan.
 */
export function ArtistTracksSection({ artistId }: { artistId: string }) {
  const cursorMapRef = useRef<Map<number, string>>(new Map());
  const [searchParams] = useSearchParams();

  // Offset comes from the raw URL page, before `total` is known — a deep link
  // has to fire the right query on a cold load.
  const rawPage = Number(searchParams.get("page") ?? "1");
  const pageParam = Number.isFinite(rawPage) && rawPage >= 1 ? rawPage : 1;
  const cursorForPage = cursorMapRef.current.get(pageParam - 1);

  const { data, isLoading, isError, error, isPlaceholderData } =
    useListTracksApiV1TracksGet(
      {
        artist_id: artistId,
        limit: PAGE_SIZE,
        offset: (pageParam - 1) * PAGE_SIZE,
        ...(cursorForPage ? { cursor: cursorForPage } : {}),
      },
      { query: { staleTime: 30_000, placeholderData: (prev) => prev } },
    );

  const response = data?.status === 200 ? data.data : undefined;
  const tracks = response?.data ?? [];
  const total = response?.total ?? 0;

  const nextCursor = response?.next_cursor;
  useEffect(() => {
    if (nextCursor) cursorMapRef.current.set(pageParam, nextCursor);
  }, [nextCursor, pageParam]);

  const { page, totalPages, setPage } = usePagination(total, {
    defaultLimit: PAGE_SIZE,
  });

  return (
    <QueryStates
      loading={isLoading}
      isError={isError}
      error={error}
      errorHeading="Failed to load tracks"
      skeleton={
        <ListRowsSkeleton
          rows={5}
          bars={["h-5 w-56", "h-5 w-32", "h-5 w-16"]}
        />
      }
      isEmpty={tracks.length === 0}
      empty={
        <EmptyState
          icon={<Music className="size-8" />}
          heading="No tracks yet"
          description="No track in your library is credited to this artist."
        />
      }
    >
      <div
        className={isPlaceholderData ? "opacity-70 transition-all" : undefined}
      >
        <TrackTable tracks={tracks} />
        <TablePagination
          page={page}
          totalPages={totalPages}
          total={total}
          limit={PAGE_SIZE}
          onPageChange={setPage}
        />
      </div>
    </QueryStates>
  );
}
