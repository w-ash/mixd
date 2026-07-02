import type { useQueryClient } from "@tanstack/react-query";
import {
  getGetPlaylistApiV1PlaylistsPlaylistIdGetQueryKey,
  getListPlaylistLinksApiV1PlaylistsPlaylistIdLinksGetQueryKey,
  getListPlaylistsApiV1PlaylistsGetQueryKey,
} from "#/api/generated/playlists/playlists";

export function invalidateLinkQueries(
  queryClient: ReturnType<typeof useQueryClient>,
  playlistId: string,
) {
  queryClient.invalidateQueries({
    queryKey:
      getListPlaylistLinksApiV1PlaylistsPlaylistIdLinksGetQueryKey(playlistId),
  });
  queryClient.invalidateQueries({
    queryKey: getGetPlaylistApiV1PlaylistsPlaylistIdGetQueryKey(playlistId),
  });
  queryClient.invalidateQueries({
    queryKey: getListPlaylistsApiV1PlaylistsGetQueryKey(),
  });
}
