import { useQueryClient } from "@tanstack/react-query";
import { ExternalLink, HelpCircle } from "lucide-react";
import { useRef } from "react";
import { Link, useParams } from "react-router";

import { ApiError } from "#/api/client";
import type { getArtistDetailApiV1ArtistsArtistIdGetResponse } from "#/api/generated/artists/artists";
import {
  getGetArtistDetailApiV1ArtistsArtistIdGetQueryKey,
  useFavoriteArtistApiV1ArtistsArtistIdFavoritePost,
  useGetArtistDetailApiV1ArtistsArtistIdGet,
  useUnfavoriteArtistApiV1ArtistsArtistIdFavoriteDelete,
} from "#/api/generated/artists/artists";
import { ArtistTracksSection } from "#/components/artist/ArtistTracksSection";
import { PageHeader } from "#/components/layout/PageHeader";
import { BackLink } from "#/components/shared/BackLink";
import { ConnectorListItem } from "#/components/shared/ConnectorListItem";
import { EmptyState } from "#/components/shared/EmptyState";
import { FavoriteToggle } from "#/components/shared/FavoriteToggle";
import { QueryErrorState } from "#/components/shared/QueryErrorState";
import {
  CardGridSkeleton,
  DetailHeaderSkeleton,
} from "#/components/shared/skeletons";
import { Badge } from "#/components/ui/badge";
import { formatCount } from "#/lib/format";
import { matchMethodDescription, matchMethodLabel } from "#/lib/match-methods";
import { pluralSuffix } from "#/lib/pluralize";

/** The `{data, status, headers}` envelope the detail query caches. */
type DetailEnvelope = getArtistDetailApiV1ArtistsArtistIdGetResponse;

const smallBadge = "text-[10px] px-1.5 py-0";

// Not keyed on the generated `ArtistKind` union: a kind written by a newer
// backend renders its own name rather than failing to build.
const KIND_LABELS: Record<string, string> = {
  person: "Person",
  group: "Group",
  other: "Other",
};

function DetailSkeleton() {
  return (
    <div className="space-y-6">
      <DetailHeaderSkeleton subtitleWidth="w-48" />
      <CardGridSkeleton count={2} gridClassName="grid-cols-2" />
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
  className,
}: {
  title: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section
      className={`rounded-lg border-l-2 border-primary/30 bg-surface-sunken p-5${className ? ` ${className}` : ""}`}
    >
      <h2 className="mb-3 font-display text-xs font-medium uppercase tracking-wider text-text-muted">
        {title}
      </h2>
      {children}
    </section>
  );
}

/** Relations read off connector payloads — `same_as` reads as "same as". */
function relationLabel(relation: string): string {
  return relation.replace(/_/g, " ");
}

export function ArtistDetail() {
  const { id } = useParams<{ id: string }>();
  const artistId = id ?? "";

  const queryClient = useQueryClient();
  const detailKey = getGetArtistDetailApiV1ArtistsArtistIdGetQueryKey(artistId);

  const { data, isLoading, isError, error } =
    useGetArtistDetailApiV1ArtistsArtistIdGet(artistId, {
      query: { staleTime: 2 * 60_000 },
    });

  // The heart flips before the request lands and flips back if it fails. The
  // pre-write envelope is held here rather than in a mutation context so both
  // mutations share one rollback without widening their generated generics.
  const rollbackRef = useRef<DetailEnvelope | undefined>(undefined);

  const applyFavorite = async (next: boolean) => {
    await queryClient.cancelQueries({ queryKey: detailKey });
    rollbackRef.current = queryClient.getQueryData<DetailEnvelope>(detailKey);
    queryClient.setQueryData<DetailEnvelope>(detailKey, (old) =>
      old?.status === 200
        ? { ...old, data: { ...old.data, is_favorited: next } }
        : old,
    );
  };

  const rollbackFavorite = () => {
    if (rollbackRef.current) {
      queryClient.setQueryData(detailKey, rollbackRef.current);
    }
  };

  const settleFavorite = () => {
    rollbackRef.current = undefined;
    void queryClient.invalidateQueries({ queryKey: detailKey });
    // The list page reads the same favorite flag.
    void queryClient.invalidateQueries({ queryKey: ["/api/v1/artists"] });
  };

  const favorite = useFavoriteArtistApiV1ArtistsArtistIdFavoritePost({
    mutation: {
      onMutate: () => applyFavorite(true),
      onError: rollbackFavorite,
      onSettled: settleFavorite,
      meta: { errorLabel: "Failed to favorite artist" },
    },
  });

  const unfavorite = useUnfavoriteArtistApiV1ArtistsArtistIdFavoriteDelete({
    mutation: {
      onMutate: () => applyFavorite(false),
      onError: rollbackFavorite,
      onSettled: settleFavorite,
      meta: { errorLabel: "Failed to remove favorite" },
    },
  });

  if (isLoading) return <DetailSkeleton />;

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
            onToggle={() =>
              isFavorited
                ? unfavorite.mutate({ artistId })
                : favorite.mutate({ artistId })
            }
          />
        }
      />

      {/* Core metadata */}
      <dl className="mb-6 flex flex-wrap gap-x-4 gap-y-2 lg:gap-x-6">
        <Field label="Kind">
          {artist.kind ? (KIND_LABELS[artist.kind] ?? artist.kind) : "—"}
        </Field>
        <Field label="MusicBrainz">
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
        </Field>
        <Field label="Tracks">
          <span className="tabular-nums">
            {formatCount(trackCount)} track{pluralSuffix(trackCount)}
          </span>
        </Field>
      </dl>

      <div className="grid gap-4 md:grid-cols-2">
        {/* Connector mappings — one row per service that knows this artist */}
        <Section title="Connectors">
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
                    {m.is_primary && (
                      <Badge variant="default" className={smallBadge}>
                        Primary
                      </Badge>
                    )}
                    {m.match_method && (
                      <Badge
                        variant="outline"
                        className={smallBadge}
                        title={matchMethodDescription(m.match_method)}
                      >
                        {matchMethodLabel(m.match_method)}
                      </Badge>
                    )}
                  </div>
                </ConnectorListItem>
              ))}
            </div>
          )}
        </Section>

        {/* Related projects — aliases, other spellings, band membership.
            Absent rather than empty: a connector that said nothing about an
            artist's relations is not the same as an artist who has none. */}
        {related.length > 0 && (
          <Section title="Related Projects">
            <ul className="space-y-2">
              {related.map((r) => (
                <li
                  key={`${r.connector_name}-${r.relation}-${r.name}`}
                  className="flex flex-wrap items-center gap-2"
                >
                  <span className="text-sm text-text">{r.name}</span>
                  <Badge variant="outline" className={smallBadge}>
                    {relationLabel(r.relation)}
                  </Badge>
                  <span className="text-xs text-text-faint">
                    {r.connector_name}
                  </span>
                </li>
              ))}
            </ul>
          </Section>
        )}

        <Section title="Tracks" className="md:col-span-2">
          <ArtistTracksSection artistId={artistId} />
        </Section>
      </div>
    </div>
  );
}
