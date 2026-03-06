import { Link, useParams } from "react-router";

import { useGetTrackDetailApiV1TracksTrackIdGet } from "@/api/generated/tracks/tracks";
import { PageHeader } from "@/components/layout/PageHeader";
import { ConnectorIcon } from "@/components/shared/ConnectorIcon";
import { EmptyState } from "@/components/shared/EmptyState";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { formatDateTime, formatDuration } from "@/lib/format";

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
    <section className="rounded-lg border border-border bg-surface-sunken p-5">
      <h2 className="mb-3 font-display text-sm font-semibold uppercase tracking-wider text-text-muted">
        {title}
      </h2>
      {children}
    </section>
  );
}

export function TrackDetail() {
  const { id } = useParams<{ id: string }>();
  const trackId = Number(id);

  const { data, isLoading, isError } = useGetTrackDetailApiV1TracksTrackIdGet(
    trackId,
    {
      query: { staleTime: 2 * 60_000 },
    },
  );

  if (isLoading) return <DetailSkeleton />;

  if (isError) {
    return (
      <EmptyState
        icon="?"
        heading="Track not found"
        description="This track doesn't exist or has been removed."
      />
    );
  }

  const track = data?.status === 200 ? data.data : undefined;
  if (!track) return null;

  const likeEntries = Object.entries(track.like_status);
  const hasPlays = track.play_summary.total_plays > 0;

  return (
    <div>
      <div className="mb-2">
        <Link
          to="/library"
          className="text-sm text-text-muted hover:text-text transition-colors"
        >
          &larr; Back to Library
        </Link>
      </div>

      <PageHeader
        title={track.title}
        description={track.artists.map((a) => a.name).join(", ")}
      />

      {/* Core metadata */}
      <dl className="mb-6 flex flex-wrap gap-x-8 gap-y-3">
        {track.album && <Field label="Album">{track.album}</Field>}
        <Field label="Duration">{formatDuration(track.duration_ms)}</Field>
        {track.release_date && (
          <Field label="Release Date">{track.release_date}</Field>
        )}
        {track.isrc && (
          <Field label="ISRC">
            <code className="font-mono text-xs">{track.isrc}</code>
          </Field>
        )}
      </dl>

      <div className="grid gap-4 md:grid-cols-2">
        {/* Connector Mappings */}
        <Section title="Connectors">
          {track.connector_mappings.length === 0 ? (
            <p className="text-sm text-text-muted">No connector mappings.</p>
          ) : (
            <ul className="space-y-2">
              {track.connector_mappings.map((m) => (
                <li
                  key={`${m.connector_name}-${m.connector_track_id}`}
                  className="flex items-center justify-between"
                >
                  <ConnectorIcon name={m.connector_name} />
                  <code className="text-xs text-text-muted font-mono">
                    {m.connector_track_id}
                  </code>
                </li>
              ))}
            </ul>
          )}
        </Section>

        {/* Like Status */}
        <Section title="Like Status">
          {likeEntries.length === 0 ? (
            <p className="text-sm text-text-muted">No like data.</p>
          ) : (
            <ul className="space-y-2">
              {likeEntries.map(([service, status]) => (
                <li
                  key={service}
                  className="flex items-center justify-between text-sm"
                >
                  <span className="capitalize text-text">{service}</span>
                  <span className="flex items-center gap-2">
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
                    {status.liked_at && (
                      <span className="text-xs text-text-muted">
                        {formatDateTime(status.liked_at)}
                      </span>
                    )}
                  </span>
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
                      {p.description}
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
