import { HelpCircle } from "lucide-react";
import { Link, useParams } from "react-router";
import { ApiError } from "#/api/client";
import {
  useAddTrackTagApiV1TracksTrackIdTagsPost,
  useDeleteTrackPreferenceApiV1TracksTrackIdPreferenceDelete,
  useDeleteTrackTagApiV1TracksTrackIdTagsTagDelete,
  useGetTrackDetailApiV1TracksTrackIdGet,
  useSetTrackPreferenceApiV1TracksTrackIdPreferencePut,
} from "#/api/generated/tracks/tracks";
import { PageHeader } from "#/components/layout/PageHeader";
import { TrackPlaysSection } from "#/components/plays/TrackPlaysSection";
import { ArtistCredits } from "#/components/shared/ArtistCredits";
import { BackLink } from "#/components/shared/BackLink";
import { DetailField, DetailSection } from "#/components/shared/detail";
import { EmptyState } from "#/components/shared/EmptyState";
import { MergeTrackDialog } from "#/components/shared/MergeTrackDialog";
import { PreferenceToggle } from "#/components/shared/PreferenceToggle";
import { QueryErrorState } from "#/components/shared/QueryErrorState";
import { DetailSkeleton } from "#/components/shared/skeletons";
import { TagEditor } from "#/components/shared/TagEditor";
import { MappingList } from "#/components/track/MappingList";
import { Badge } from "#/components/ui/badge";
import {
  decodeHtmlEntities,
  formatCount,
  formatDate,
  formatDateTime,
  formatDuration,
} from "#/lib/format";

export function TrackDetail() {
  const { id } = useParams<{ id: string }>();
  const trackId = id ?? "";

  const { data, isLoading, isError, error } =
    useGetTrackDetailApiV1TracksTrackIdGet(trackId, {
      query: { staleTime: 2 * 60_000 },
    });

  const setPref = useSetTrackPreferenceApiV1TracksTrackIdPreferencePut({
    mutation: {
      meta: { errorLabel: "Failed to set preference" },
    },
  });

  const deletePref = useDeleteTrackPreferenceApiV1TracksTrackIdPreferenceDelete(
    {
      mutation: {
        meta: { errorLabel: "Failed to clear preference" },
      },
    },
  );

  const handlePreferenceChange = (
    state: "hmm" | "nah" | "yah" | "star" | null,
  ) => {
    if (state === null) {
      deletePref.mutate({ trackId });
    } else {
      setPref.mutate({ trackId, data: { state } });
    }
  };

  const addTag = useAddTrackTagApiV1TracksTrackIdTagsPost({
    mutation: {
      meta: { errorLabel: "Failed to add tag" },
    },
  });

  const deleteTag = useDeleteTrackTagApiV1TracksTrackIdTagsTagDelete({
    mutation: {
      meta: { errorLabel: "Failed to remove tag" },
    },
  });

  if (isLoading) return <DetailSkeleton cards={4} />;

  if (isError) {
    const is404 = error instanceof ApiError && error.status === 404;
    if (!is404)
      return <QueryErrorState error={error} heading="Failed to load track" />;

    return (
      <EmptyState
        icon={<HelpCircle className="size-10" />}
        heading="Track not found"
        description="This track doesn't exist or has been removed."
        role="alert"
      />
    );
  }

  const track = data?.status === 200 ? data.data : undefined;
  if (!track) return null;

  const likeEntries = Object.entries(track.like_status);
  const hasPlays = track.play_summary.total_plays > 0;

  return (
    <div>
      <title>{track.title} — Mixd</title>
      <BackLink to="/library">Library</BackLink>

      <PageHeader
        title={track.title}
        description={<ArtistCredits artists={track.artists} />}
        action={<MergeTrackDialog winner={track} />}
      />

      {/* Core metadata */}
      <dl className="mb-6 flex flex-wrap gap-x-4 gap-y-2 lg:gap-x-6">
        {track.album && <DetailField label="Album">{track.album}</DetailField>}
        <DetailField label="Duration">
          {formatDuration(track.duration_ms)}
        </DetailField>
        {track.release_date && (
          <DetailField label="Release Date">
            {formatDate(track.release_date)}
          </DetailField>
        )}
        {track.isrc && (
          <DetailField label="ISRC">
            <code className="font-mono text-xs">{track.isrc}</code>
          </DetailField>
        )}
      </dl>

      {/* Preference */}
      <div className="mb-6 flex items-center gap-3">
        <span className="text-xs font-medium uppercase tracking-wider text-text-faint">
          Preference
        </span>
        <PreferenceToggle
          value={track.preference ?? null}
          onChange={handlePreferenceChange}
          disabled={setPref.isPending || deletePref.isPending}
        />
      </div>

      <div className="mb-6 flex items-start gap-3">
        <span className="pt-1.5 text-xs font-medium uppercase tracking-wider text-text-faint">
          Tags
        </span>
        <TagEditor
          value={track.tags ?? []}
          onAdd={(rawTag) => addTag.mutate({ trackId, data: { tag: rawTag } })}
          onRemove={(tag) =>
            // Orval does not encode path params — tags can contain `:` / `/`,
            // so encode here before the DELETE hits the route.
            deleteTag.mutate({ trackId, tag: encodeURIComponent(tag) })
          }
          disabled={addTag.isPending || deleteTag.isPending}
        />
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        {/* Connector Mappings with Provenance */}
        <DetailSection title="Connectors">
          {track.connector_mappings.length === 0 ? (
            <p className="text-sm text-text-muted">No connector mappings.</p>
          ) : (
            <MappingList
              trackId={track.id}
              mappings={track.connector_mappings}
              trackTitle={track.title}
            />
          )}
        </DetailSection>

        {/* Like Status */}
        <DetailSection title="Like Status">
          {likeEntries.length === 0 ? (
            <p className="text-sm text-text-muted">No like data.</p>
          ) : (
            <ul className="grid grid-cols-[auto_auto_1fr] items-center gap-x-3 gap-y-2 text-sm">
              {likeEntries.map(([service, status]) => (
                <li
                  key={service}
                  className="col-span-3 grid grid-cols-subgrid items-center"
                >
                  <span className="capitalize text-text">{service}</span>
                  {status.is_liked ? (
                    <Badge
                      variant="default"
                      className="bg-status-liked/20 text-status-liked border-status-liked/30"
                    >
                      Liked
                    </Badge>
                  ) : (
                    <Badge variant="outline" className="text-text-muted">
                      Not liked
                    </Badge>
                  )}
                  {status.liked_at ? (
                    <span className="text-xs text-text-muted">
                      {formatDateTime(status.liked_at)}
                    </span>
                  ) : (
                    <span />
                  )}
                </li>
              ))}
            </ul>
          )}
        </DetailSection>

        {/* Play Summary */}
        <DetailSection title="Play History">
          {!hasPlays ? (
            <p className="text-sm text-text-muted">No play history recorded.</p>
          ) : (
            <>
              <dl className="space-y-2">
                <DetailField label="Total Plays">
                  <span className="tabular-nums">
                    {formatCount(track.play_summary.total_plays)}
                  </span>
                </DetailField>
                <DetailField label="First Played">
                  {formatDateTime(track.play_summary.first_played)}
                </DetailField>
                <DetailField label="Last Played">
                  {formatDateTime(track.play_summary.last_played)}
                </DetailField>
              </dl>
              <TrackPlaysSection
                trackId={track.id}
                totalPlays={track.play_summary.total_plays}
              />
            </>
          )}
        </DetailSection>

        {/* Playlists */}
        <DetailSection title="Playlists">
          {track.playlists.length === 0 ? (
            <p className="text-sm text-text-muted">Not in any playlists.</p>
          ) : (
            <ul className="space-y-1">
              {track.playlists.map((p) => (
                <li key={p.id}>
                  <Link
                    to={`/playlists/${p.id}`}
                    className="text-sm text-text hover:text-primary transition-colors"
                  >
                    {p.name}
                  </Link>
                  {p.description && (
                    <p className="text-xs text-text-muted line-clamp-1">
                      {decodeHtmlEntities(p.description)}
                    </p>
                  )}
                </li>
              ))}
            </ul>
          )}
        </DetailSection>
      </div>
    </div>
  );
}
