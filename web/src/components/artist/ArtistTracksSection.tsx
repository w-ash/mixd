import { Music } from "lucide-react";

import { useListTracksApiV1TracksGet } from "#/api/generated/tracks/tracks";
import { TrackTable } from "#/components/library/TrackTable";
import { EmptyState } from "#/components/shared/EmptyState";
import { QueryStates } from "#/components/shared/QueryStates";
import { ListRowsSkeleton } from "#/components/shared/skeletons";
import { TablePagination } from "#/components/shared/TablePagination";
import { useKeysetPagination } from "#/hooks/usePagination";

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
  const {
    page,
    limit,
    offset,
    cursor,
    totalPages,
    setPage,
    rememberNextCursor,
  } = useKeysetPagination({ defaultLimit: PAGE_SIZE });

  const { data, isLoading, isError, error, isPlaceholderData } =
    useListTracksApiV1TracksGet(
      {
        artist_id: artistId,
        limit,
        offset,
        ...(cursor ? { cursor } : {}),
      },
      { query: { staleTime: 30_000, placeholderData: (prev) => prev } },
    );

  const response = data?.status === 200 ? data.data : undefined;
  const tracks = response?.data ?? [];
  const total = response?.total ?? 0;
  rememberNextCursor(response);

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
          limit={limit}
          onPageChange={setPage}
        />
      </div>
    </QueryStates>
  );
}
