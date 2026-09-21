import { ExternalLink, HelpCircle } from "lucide-react";
import { Link, useParams } from "react-router";

import { ApiError } from "#/api/client";
import { useGetArtistDetailApiV1ArtistsArtistIdGet } from "#/api/generated/artists/artists";
import { ArtistTracksSection } from "#/components/artist/ArtistTracksSection";
import { PageHeader } from "#/components/layout/PageHeader";
import { BackLink } from "#/components/shared/BackLink";
import { ConnectorListItem } from "#/components/shared/ConnectorListItem";
import { DetailField, DetailSection } from "#/components/shared/detail";
import { EmptyState } from "#/components/shared/EmptyState";
import { FavoriteToggle } from "#/components/shared/FavoriteToggle";
import {
  MatchMethodBadge,
  PrimaryBadge,
  SMALL_BADGE,
} from "#/components/shared/MappingBadges";
import { QueryErrorState } from "#/components/shared/QueryErrorState";
import { DetailSkeleton } from "#/components/shared/skeletons";
import { Badge } from "#/components/ui/badge";
import { useArtistFavorite } from "#/hooks/useArtistFavorite";
import { formatCount } from "#/lib/format";
import { pluralSuffix } from "#/lib/pluralize";

// Not keyed on the generated `ArtistKind` union: a kind written by a newer
// backend renders its own name rather than failing to build.
const KIND_LABELS: Record<string, string> = {
  person: "Person",
  group: "Group",
  other: "Other",
};

/** Relations read off connector payloads — `same_as` reads as "same as". */
function relationLabel(relation: string): string {
  return relation.replace(/_/g, " ");
}

/** The artist's library tracks — its own query, so it loads independently. */
function TracksSection({ artistId }: { artistId: string }) {
  return (
    <DetailSection title="Tracks" className="md:col-span-2">
      <ArtistTracksSection artistId={artistId} />
    </DetailSection>
  );
}

export function ArtistDetail() {
  const { id } = useParams<{ id: string }>();
  const artistId = id ?? "";

  const { data, isLoading, isError, error } =
    useGetArtistDetailApiV1ArtistsArtistIdGet(artistId, {
      query: { staleTime: 2 * 60_000 },
    });

  const { toggle } = useArtistFavorite();

  // The tracks section mounts under the skeleton rather than after it: its
  // query is independent of this one, so making it wait would serialize two
  // requests that can run together.
  if (isLoading) {
    return (
      <div className="space-y-4">
        <DetailSkeleton cards={2} />
        <TracksSection artistId={artistId} />
      </div>
    );
  }

  if (isError) {
    const is404 = error instanceof ApiError && error.status === 404;
    if (!is404)
      return <QueryErrorState error={error} heading="Failed to load artist" />;

    return (
      <EmptyState
        icon={<HelpCircle className="size-10" />}
        heading="Artist not found"
        description="This artist doesn't exist or has been removed."
        role="alert"
      />
    );
  }

  const artist = data?.status === 200 ? data.data : undefined;
  if (!artist) return null;

  const isFavorited = artist.is_favorited ?? false;
  const mappings = artist.connector_mappings ?? [];
  const related = artist.related ?? [];
  const trackCount = artist.track_count ?? 0;

  return (
    <div>
      <title>{artist.name} — Mixd</title>
      <BackLink to="/artists">Artists</BackLink>

      <PageHeader
        title={artist.name}
        action={
          <FavoriteToggle
            isFavorited={isFavorited}
            label={artist.name}
            onToggle={() => toggle(artistId, isFavorited)}
          />
        }
      />

      {/* Core metadata */}
      <dl className="mb-6 flex flex-wrap gap-x-4 gap-y-2 lg:gap-x-6">
        <DetailField label="Kind">
          {artist.kind ? (KIND_LABELS[artist.kind] ?? artist.kind) : "—"}
        </DetailField>
        <DetailField label="MusicBrainz">
          {artist.mbid ? (
            <a
              href={`https://musicbrainz.org/artist/${artist.mbid}`}
              target="_blank"
              rel="noopener noreferrer"
              className="font-mono text-xs text-text transition-colors hover:text-primary"
            >
              {artist.mbid}
            </a>
          ) : (
            "—"
          )}
        </DetailField>
        <DetailField label="Tracks">
          <span className="tabular-nums">
            {formatCount(trackCount)} track{pluralSuffix(trackCount)}
          </span>
        </DetailField>
      </dl>

      <div className="grid gap-4 md:grid-cols-2">
        {/* Connector mappings — one row per service that knows this artist */}
        <DetailSection title="Connectors">
          {mappings.length === 0 ? (
            <div className="space-y-1">
              <p className="text-sm text-text-muted">
                Not mapped on any service yet.
              </p>
              <p className="text-xs text-text-faint">
                Run{" "}
                <Link
                  to="/settings/sync"
                  className="text-text-muted underline underline-offset-2 transition-colors hover:text-primary"
                >
                  Enrich Artists
                </Link>{" "}
                from the Import Center to look this artist up on MusicBrainz.
              </p>
            </div>
          ) : (
            <div className="space-y-2">
              {mappings.map((m) => (
                <ConnectorListItem
                  key={`${m.connector_name}-${m.connector_artist_identifier}`}
                  connectorName={m.connector_name}
                  muted={!m.is_primary}
                  actions={
                    m.external_url ? (
                      <a
                        href={m.external_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex h-7 items-center px-2 text-xs text-text-faint transition-colors hover:text-text"
                      >
                        <ExternalLink className="mr-1 size-3" />
                        Open
                      </a>
                    ) : undefined
                  }
                >
                  <span className="text-sm font-medium text-text">
                    {m.name}
                  </span>
                  <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                    {m.is_primary && <PrimaryBadge />}
                    {m.match_method && (
                      <MatchMethodBadge method={m.match_method} />
                    )}
                  </div>
                </ConnectorListItem>
              ))}
            </div>
          )}
        </DetailSection>

        {/* Related projects — aliases, other spellings, band membership.
            Absent rather than empty: a connector that said nothing about an
            artist's relations is not the same as an artist who has none. */}
        {related.length > 0 && (
          <DetailSection title="Related Projects">
            <ul className="space-y-2">
              {related.map((r) => (
                <li
                  key={`${r.connector_name}-${r.relation}-${r.name}`}
                  className="flex flex-wrap items-center gap-2"
                >
                  <span className="text-sm text-text">{r.name}</span>
                  <Badge variant="outline" className={SMALL_BADGE}>
                    {relationLabel(r.relation)}
                  </Badge>
                  <span className="text-xs text-text-faint">
                    {r.connector_name}
                  </span>
                </li>
              ))}
            </ul>
          </DetailSection>
        )}

        <TracksSection artistId={artistId} />
      </div>
    </div>
  );
}
