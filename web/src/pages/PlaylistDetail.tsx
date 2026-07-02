import { HelpCircle, Music } from "lucide-react";
import { useParams } from "react-router";
import {
  useGetPlaylistApiV1PlaylistsPlaylistIdGet,
  useGetPlaylistTracksApiV1PlaylistsPlaylistIdTracksGet,
} from "#/api/generated/playlists/playlists";
import { STALE } from "#/api/query-client";
import { PageHeader } from "#/components/layout/PageHeader";
import { AddTracksDialog } from "#/components/playlist/AddTracksDialog";
import { DeletePlaylistDialog } from "#/components/playlist/DeletePlaylistDialog";
import { EditPlaylistDialog } from "#/components/playlist/EditPlaylistDialog";
import { LinkedServicesSection } from "#/components/playlist/LinkedServicesSection";
import { PlaylistTrackEditor } from "#/components/playlist/PlaylistTrackEditor";
import { RepairUnresolvedBar } from "#/components/playlist/RepairUnresolvedBar";
import { BackLink } from "#/components/shared/BackLink";
import { ConnectorIcon } from "#/components/shared/ConnectorIcon";
import { EmptyState } from "#/components/shared/EmptyState";
import {
  DetailHeaderSkeleton,
  ListRowsSkeleton,
} from "#/components/shared/skeletons";
import { Skeleton } from "#/components/ui/skeleton";
import {
  decodeHtmlEntities,
  formatDate,
  formatTotalDuration,
} from "#/lib/format";
import { pluralize } from "#/lib/pluralize";

function DetailSkeleton() {
  return (
    <div className="space-y-6">
      <DetailHeaderSkeleton />
      <ListRowsSkeleton
        rows={8}
        bars={["h-5 w-8", "h-5 w-56", "h-5 w-36", "h-5 w-40", "h-5 w-12"]}
      />
    </div>
  );
}

// ─── Main Page ───────────────────────────────────────────────────────────────

export function PlaylistDetail() {
  const { id } = useParams<{ id: string }>();
  const playlistId = id ?? "";

  const {
    data: playlistData,
    isLoading: playlistLoading,
    isError: playlistError,
  } = useGetPlaylistApiV1PlaylistsPlaylistIdGet(playlistId, {
    query: { staleTime: STALE.MEDIUM },
  });

  const { data: tracksData, isLoading: tracksLoading } =
    useGetPlaylistTracksApiV1PlaylistsPlaylistIdTracksGet(
      playlistId,
      undefined,
      {
        query: { staleTime: STALE.MEDIUM },
      },
    );

  if (playlistLoading) return <DetailSkeleton />;

  if (playlistError) {
    return (
      <EmptyState
        icon={<HelpCircle className="size-10" />}
        heading="Playlist not found"
        description="This playlist doesn't exist or has been deleted."
      />
    );
  }

  const playlist = playlistData?.status === 200 ? playlistData.data : undefined;
  if (!playlist) return null;

  const tracksResponse =
    tracksData?.status === 200 ? tracksData.data : undefined;
  const entries = tracksResponse?.data ?? [];
  const unresolvedCount = entries.filter((e) => e.is_resolved === false).length;
  // Track ids already in the playlist — drives the "Added" badge in the
  // Add-Tracks modal (re-adding is still allowed; duplicates are intentional).
  const existingTrackIds = new Set(
    entries
      .map((e) => e.track.id)
      .filter((id): id is string => typeof id === "string"),
  );

  return (
    <div>
      <title>{playlist.name} — Mixd</title>
      <BackLink to="/playlists">Playlists</BackLink>
      <PageHeader
        title={playlist.name}
        description={
          playlist.description
            ? decodeHtmlEntities(playlist.description)
            : undefined
        }
        action={
          <div className="flex gap-2">
            <AddTracksDialog
              playlistId={playlist.id}
              existingTrackIds={existingTrackIds}
            />
            <EditPlaylistDialog
              playlistId={playlist.id}
              currentName={playlist.name}
              currentDescription={playlist.description}
            />
            <DeletePlaylistDialog playlistId={playlist.id} />
          </div>
        }
      />

      <div className="mb-6 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-text-muted">
        <span>{pluralize(playlist.track_count, "track")}</span>
        {entries.length > 0 && (
          <>
            <span aria-hidden="true">&middot;</span>
            <span>
              {formatTotalDuration(
                entries.reduce((sum, e) => sum + (e.track.duration_ms ?? 0), 0),
              )}
            </span>
          </>
        )}
        {playlist.updated_at && (
          <>
            <span aria-hidden="true">&middot;</span>
            <span>Updated {formatDate(playlist.updated_at)}</span>
          </>
        )}
        {playlist.connector_links.length > 0 && (
          <>
            <span aria-hidden="true">&middot;</span>
            <span className="flex items-center gap-2">
              {playlist.connector_links.map((link) => (
                <ConnectorIcon
                  key={link.connector_name}
                  name={link.connector_name}
                />
              ))}
            </span>
          </>
        )}
      </div>

      {/* Linked Services */}
      <LinkedServicesSection playlistId={playlistId} />

      {/* Unresolved tracks — first-class, with bulk repair */}
      <RepairUnresolvedBar playlistId={playlistId} count={unresolvedCount} />

      {tracksLoading && (
        <div className="space-y-3">
          {Array.from({ length: 5 }).map((_, i) => (
            // biome-ignore lint/suspicious/noArrayIndexKey: static skeleton placeholders
            <div key={i} className="flex items-center gap-4">
              <Skeleton className="h-5 w-8" />
              <Skeleton className="h-5 w-56" />
              <Skeleton className="h-5 w-36" />
              <Skeleton className="h-5 w-40" />
              <Skeleton className="h-5 w-12" />
            </div>
          ))}
        </div>
      )}

      {!tracksLoading && entries.length === 0 && (
        <EmptyState
          icon={<Music className="size-10" />}
          heading="This playlist is empty"
          description="Add tracks with the Add Tracks button, link a connector playlist, or build it with a workflow."
        />
      )}

      {!tracksLoading && entries.length > 0 && (
        <PlaylistTrackEditor playlistId={playlistId} entries={entries} />
      )}
    </div>
  );
}
