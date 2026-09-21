/**
 * One optimistic favorite toggle for every surface that shows an artist heart.
 *
 * The heart flips in the cache before the request leaves and flips back if it
 * fails. Both the list pages and the detail page are rewritten on every toggle,
 * so a favorite made on one is already true on the other when it is reached —
 * whichever page owns the click.
 *
 * Nothing is invalidated here: the route-derived cache tags on the generated
 * mutations dirty the whole `artists` family, so the global
 * `MutationCache.onSuccess` refetches the truth for both queries.
 */

import { useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";

import type {
  getArtistDetailApiV1ArtistsArtistIdGetResponse,
  listArtistsApiV1ArtistsGetResponse,
} from "#/api/generated/artists/artists";
import {
  getGetArtistDetailApiV1ArtistsArtistIdGetQueryKey,
  useFavoriteArtistApiV1ArtistsArtistIdFavoritePost,
  useUnfavoriteArtistApiV1ArtistsArtistIdFavoriteDelete,
} from "#/api/generated/artists/artists";

/**
 * Prefix every `GET /artists` list query shares. Orval keys a query on its
 * request path followed by its params, so this partial key reaches every
 * cached page and filter combination at once — which is what an optimistic
 * favorite flip has to touch, since the row is visible under several of them.
 */
export const ARTIST_LIST_KEY = ["/api/v1/artists"] as const;

type ListEnvelope = listArtistsApiV1ArtistsGetResponse;
type DetailEnvelope = getArtistDetailApiV1ArtistsArtistIdGetResponse;

/** Pre-write cache contents, kept for a rollback. */
interface FavoriteSnapshot {
  lists: [readonly unknown[], ListEnvelope | undefined][];
  detailKey: readonly unknown[];
  detail: DetailEnvelope | undefined;
}

/** Rewrite one artist's favorite flag in a cached list page. */
function withListFavorite(
  cached: ListEnvelope | undefined,
  artistId: string,
  isFavorited: boolean,
): ListEnvelope | undefined {
  if (cached?.status !== 200) return cached;
  return {
    ...cached,
    data: {
      ...cached.data,
      data: cached.data.data.map((artist) =>
        artist.id === artistId
          ? { ...artist, is_favorited: isFavorited }
          : artist,
      ),
    },
  };
}

export interface UseArtistFavoriteResult {
  /** Flip `artistId`, where `isFavorited` is the state it is flipping FROM. */
  toggle: (artistId: string, isFavorited: boolean) => void;
}

export function useArtistFavorite(): UseArtistFavoriteResult {
  const queryClient = useQueryClient();

  const applyFavorite = useCallback(
    async (artistId: string, next: boolean): Promise<FavoriteSnapshot> => {
      const detailKey =
        getGetArtistDetailApiV1ArtistsArtistIdGetQueryKey(artistId);
      // An in-flight detail read would land after the flip and undo it.
      await queryClient.cancelQueries({ queryKey: detailKey });

      // `setQueriesData` hands back what it WROTE, so the snapshots for a
      // rollback have to be read before the write, not taken from its result.
      const snapshot: FavoriteSnapshot = {
        lists: queryClient.getQueriesData<ListEnvelope>({
          queryKey: ARTIST_LIST_KEY,
        }),
        detailKey,
        detail: queryClient.getQueryData<DetailEnvelope>(detailKey),
      };

      queryClient.setQueriesData<ListEnvelope>(
        { queryKey: ARTIST_LIST_KEY },
        (cached) => withListFavorite(cached, artistId, next),
      );
      queryClient.setQueryData<DetailEnvelope>(detailKey, (cached) =>
        cached?.status === 200
          ? { ...cached, data: { ...cached.data, is_favorited: next } }
          : cached,
      );

      return snapshot;
    },
    [queryClient],
  );

  const rollback = useCallback(
    (snapshot: FavoriteSnapshot | undefined) => {
      if (!snapshot) return;
      for (const [key, cached] of snapshot.lists) {
        queryClient.setQueryData(key, cached);
      }
      queryClient.setQueryData(snapshot.detailKey, snapshot.detail);
    },
    [queryClient],
  );

  const favorite = useFavoriteArtistApiV1ArtistsArtistIdFavoritePost({
    mutation: {
      meta: { errorLabel: "Failed to favorite artist" },
      onMutate: ({ artistId }) => applyFavorite(artistId, true),
      onError: (_error, _variables, context) => rollback(context),
    },
  });

  const unfavorite = useUnfavoriteArtistApiV1ArtistsArtistIdFavoriteDelete({
    mutation: {
      meta: { errorLabel: "Failed to remove favorite" },
      onMutate: ({ artistId }) => applyFavorite(artistId, false),
      onError: (_error, _variables, context) => rollback(context),
    },
  });

  const toggle = useCallback(
    (artistId: string, isFavorited: boolean) => {
      const mutation = isFavorited ? unfavorite : favorite;
      mutation.mutate({ artistId });
    },
    [favorite, unfavorite],
  );

  return { toggle };
}
