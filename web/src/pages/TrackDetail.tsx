import { useQueryClient } from "@tanstack/react-query";
import { ExternalLink, HelpCircle, Link2Off, Repeat, Star } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router";
import { toast } from "sonner";

import { ApiError } from "@/api/client";
import type { ConnectorMappingSchema } from "@/api/generated/model";
import {
  getGetTrackDetailApiV1TracksTrackIdGetQueryKey,
  useGetTrackDetailApiV1TracksTrackIdGet,
  useSetPrimaryMappingApiV1TracksTrackIdMappingsMappingIdPrimaryPatch,
} from "@/api/generated/tracks/tracks";
import { PageHeader } from "@/components/layout/PageHeader";
import { BackLink } from "@/components/shared/BackLink";
import { ConnectorListItem } from "@/components/shared/ConnectorListItem";
import { EmptyState } from "@/components/shared/EmptyState";
import { MergeTrackDialog } from "@/components/shared/MergeTrackDialog";
import { QueryErrorState } from "@/components/shared/QueryErrorState";
import { RelinkMappingDialog } from "@/components/shared/RelinkMappingDialog";
import {
  confidenceVariant,
  StatusIndicator,
} from "@/components/shared/StatusIndicator";
import { UnlinkMappingDialog } from "@/components/shared/UnlinkMappingDialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  decodeHtmlEntities,
  formatArtists,
  formatDate,
  formatDateTime,
  formatDuration,
} from "@/lib/format";

function DetailSkeleton() {
  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-4 w-48" />
      </div>
      <div className="grid grid-cols-2 gap-4">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
    </div>
  );
}

/** Labeled metadata field */
function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <dt className="text-xs font-medium uppercase tracking-wider text-text-faint">
        {label}
      </dt>
      <dd className="mt-0.5 text-sm text-text">{children}</dd>
    </div>
  );
}

/** Section card with heading */
function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-lg border-l-2 border-primary/30 bg-surface-sunken p-5">
      <h2 className="mb-3 font-display text-xs font-medium uppercase tracking-wider text-text-muted">
        {title}
      </h2>
      {children}
    </section>
  );
}

/** Build external URL for a connector track ID, or null if not linkable */
function getConnectorTrackUrl(
  connectorName: string,
  trackId: string,
): string | null {
  switch (connectorName) {
    case "spotify":
      return `https://open.spotify.com/track/${trackId}`;
    case "musicbrainz":
      return `https://musicbrainz.org/recording/${trackId}`;
    case "lastfm":
      return trackId.startsWith("https://") ? trackId : null;
    default:
      return null;
  }
}

/** Human-readable match method label + explanation */
const matchMethods: Record<string, { label: string; description: string }> = {
  direct_import: {
    label: "Direct",
    description: "Matched by ISRC (exact identifier)",
  },
  direct: {
    label: "Direct",
    description: "Matched by ISRC (exact identifier)",
  },
  search_fallback: {
    label: "Search",
    description: "Found via search by artist + title",
  },
  artist_title: {
    label: "Artist/Title",
    description: "Matched by artist name and track title",
  },
  spotify_redirect: {
    label: "Redirect",
    description: "Redirected from a different version",
  },
  spotify_connector_play_resolver: {
    label: "Play Resolver",
    description: "Resolved from listening history",
  },
  lastfm_discovery: {
    label: "Discovery",
    description: "Discovered via Last.fm data",
  },
  direct_import_stale_id: {
    label: "Stale ID",
    description: "Originally matched by ID, but the ID has since changed",
  },
  search_fallback_stale_id: {
    label: "Stale ID",
    description: "Originally found via search, but the ID has since changed",
  },
};

function matchMethodLabel(method: string): string {
  return matchMethods[method]?.label ?? method;
}

function matchMethodDescription(method: string): string {
  return matchMethods[method]?.description ?? method;
}

const smallBadge = "text-[10px] px-1.5 py-0";

/** Connector mapping list with hover-reveal actions */
function MappingList({
  trackId,
  mappings,
  trackTitle,
}: {
  trackId: string;
  mappings: ConnectorMappingSchema[];
  trackTitle: string;
}) {
  const [relinkMapping, setRelinkMapping] =
    useState<ConnectorMappingSchema | null>(null);
  const [unlinkMapping, setUnlinkMapping] =
    useState<ConnectorMappingSchema | null>(null);

  const queryClient = useQueryClient();
  const setPrimaryMutation =
    useSetPrimaryMappingApiV1TracksTrackIdMappingsMappingIdPrimaryPatch({
      mutation: {
        onSuccess: () => {
          queryClient.invalidateQueries({
            queryKey: getGetTrackDetailApiV1TracksTrackIdGetQueryKey(trackId),
          });
          toast.success("Primary mapping updated");
        },
        onError: (error: Error) => {
          toast.error("Failed to set primary", { description: error.message });
        },
      },
    });

  return (
    <>
      <div className="space-y-2">
        {mappings.map((m) => {
          const url = getConnectorTrackUrl(
            m.connector_name,
            m.connector_track_id,
          );
          const titleDiffers =
            m.connector_track_title && m.connector_track_title !== trackTitle;

          return (
            <ConnectorListItem
              key={`${m.connector_name}-${m.connector_track_id}`}
              connectorName={m.connector_name}
              muted={!m.is_primary}
              actions={
                <>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-7 px-2 text-xs text-text-faint hover:text-text"
                    onClick={() => setRelinkMapping(m)}
                  >
                    <Repeat className="mr-1 size-3" />
                    Relink
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-7 px-2 text-xs text-text-faint hover:text-destructive"
                    onClick={() => setUnlinkMapping(m)}
                  >
                    <Link2Off className="mr-1 size-3" />
                    Unlink
                  </Button>
                  {!m.is_primary && (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-7 px-2 text-xs text-text-faint hover:text-text"
                      disabled={setPrimaryMutation.isPending}
                      onClick={() =>
                        setPrimaryMutation.mutate({
                          trackId,
                          mappingId: m.mapping_id,
                        })
                      }
                    >
                      <Star className="mr-1 size-3" />
                      Primary
                    </Button>
                  )}
                  {url && (
                    <a
                      href={url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex h-7 items-center px-2 text-xs text-text-faint transition-colors hover:text-text"
                    >
                      <ExternalLink className="mr-1 size-3" />
                      Open
                    </a>
                  )}
                </>
              }
            >
              {/* Title + artists */}
              <div>
                <span className="text-sm font-medium text-text">
                  {m.connector_track_title || m.connector_track_id}
                </span>
                {m.connector_track_artists.length > 0 && (
                  <span className="ml-1.5 text-xs text-text-muted">
                    {m.connector_track_artists.join(", ")}
                  </span>
                )}
              </div>

              {/* Title mismatch warning */}
              {titleDiffers && (
                <p className="mt-1 rounded bg-status-expired/10 px-2 py-0.5 text-xs text-status-expired">
                  Service title differs: &ldquo;
                  {m.connector_track_title}&rdquo;
                </p>
              )}

              {/* Metadata badges */}
              <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                {m.is_primary && (
                  <Badge variant="default" className={smallBadge}>
                    Primary
                  </Badge>
                )}
                <Badge
                  variant="outline"
                  className={smallBadge}
                  title={matchMethodDescription(m.match_method)}
                >
                  {matchMethodLabel(m.match_method)}
                </Badge>
                <StatusIndicator
                  variant={confidenceVariant(m.confidence)}
                  label={`${m.confidence}%`}
                  size="sm"
                />
                {m.origin === "manual_override" && (
                  <Badge
                    variant="outline"
                    className={`${smallBadge} border-primary/40 text-primary`}
                  >
                    Manual
                  </Badge>
                )}
              </div>
            </ConnectorListItem>
          );
        })}
      </div>

      {relinkMapping && (
        <RelinkMappingDialog
          trackId={trackId}
          mapping={relinkMapping}
          open
          onOpenChange={(open) => !open && setRelinkMapping(null)}
        />
      )}

      {unlinkMapping && (
        <UnlinkMappingDialog
          trackId={trackId}
          mapping={unlinkMapping}
          open
          onOpenChange={(open) => !open && setUnlinkMapping(null)}
        />
      )}
    </>
  );
}

export function TrackDetail() {
  const { id } = useParams<{ id: string }>();
  const trackId = id ?? "";

  const { data, isLoading, isError, error } =
    useGetTrackDetailApiV1TracksTrackIdGet(trackId, {
      query: { staleTime: 2 * 60_000 },
    });

  if (isLoading) return <DetailSkeleton />;

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
        description={formatArtists(track.artists)}
        action={<MergeTrackDialog winner={track} />}
      />

      {/* Core metadata */}
      <dl className="mb-6 flex flex-wrap gap-x-6 gap-y-2">
        {track.album && <Field label="Album">{track.album}</Field>}
        <Field label="Duration">{formatDuration(track.duration_ms)}</Field>
        {track.release_date && (
          <Field label="Release Date">{formatDate(track.release_date)}</Field>
        )}
        {track.isrc && (
          <Field label="ISRC">
            <code className="font-mono text-xs">{track.isrc}</code>
          </Field>
        )}
      </dl>

      <div className="grid gap-4 md:grid-cols-2">
        {/* Connector Mappings with Provenance */}
        <Section title="Connectors">
          {track.connector_mappings.length === 0 ? (
            <p className="text-sm text-text-muted">No connector mappings.</p>
          ) : (
            <MappingList
              trackId={track.id}
              mappings={track.connector_mappings}
              trackTitle={track.title}
            />
          )}
        </Section>

        {/* Like Status */}
        <Section title="Like Status">
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
        </Section>

        {/* Play Summary */}
        <Section title="Play History">
          {!hasPlays ? (
            <p className="text-sm text-text-muted">No play history recorded.</p>
          ) : (
            <dl className="space-y-2">
              <Field label="Total Plays">
                <span className="tabular-nums">
                  {track.play_summary.total_plays.toLocaleString()}
                </span>
              </Field>
              <Field label="First Played">
                {formatDateTime(track.play_summary.first_played)}
              </Field>
              <Field label="Last Played">
                {formatDateTime(track.play_summary.last_played)}
              </Field>
            </dl>
          )}
        </Section>

        {/* Playlists */}
        <Section title="Playlists">
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
        </Section>
      </div>
    </div>
  );
}
