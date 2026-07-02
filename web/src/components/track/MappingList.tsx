import { useQueryClient } from "@tanstack/react-query";
import { ExternalLink, Link2Off, Repeat, Star } from "lucide-react";
import { useState } from "react";
import type { ConnectorMappingSchema } from "#/api/generated/model";
import {
  getGetTrackDetailApiV1TracksTrackIdGetQueryKey,
  useSetPrimaryMappingApiV1TracksTrackIdMappingsMappingIdPrimaryPatch,
} from "#/api/generated/tracks/tracks";
import { ConnectorListItem } from "#/components/shared/ConnectorListItem";
import { RelinkMappingDialog } from "#/components/shared/RelinkMappingDialog";
import {
  confidenceVariant,
  StatusIndicator,
} from "#/components/shared/StatusIndicator";
import { UnlinkMappingDialog } from "#/components/shared/UnlinkMappingDialog";
import { Badge } from "#/components/ui/badge";
import { Button } from "#/components/ui/button";
import { matchMethodDescription, matchMethodLabel } from "#/lib/match-methods";
import { toasts } from "#/lib/toasts";

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

const smallBadge = "text-[10px] px-1.5 py-0";

/** Connector mapping list with hover-reveal actions */
export function MappingList({
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
          toasts.success("Primary mapping updated");
        },
        meta: { errorLabel: "Failed to set primary" },
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
