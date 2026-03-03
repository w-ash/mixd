import type { ConnectorStatusSchema } from "@/api/generated/model";
import { ConnectorIcon } from "@/components/shared/ConnectorIcon";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";

/** Static descriptions — presentation concern, not API data. */
const descriptions: Record<
  string,
  { connected: string; disconnected: string }
> = {
  spotify: {
    connected: "Playlists, liked tracks, listening history",
    disconnected: "Run the CLI to connect your Spotify account.",
  },
  lastfm: {
    connected: "Scrobble counts, play history, loved tracks",
    disconnected: "Run the CLI to connect your Last.fm account.",
  },
  musicbrainz: {
    connected: "Track identification, metadata enrichment",
    disconnected: "Track identification, metadata enrichment",
  },
  apple: {
    connected: "Library, playlists",
    disconnected: "Coming soon — connector under development.",
  },
};

function getStatusBadge(connector: ConnectorStatusSchema) {
  // Apple Music — always "Coming soon" regardless of connected state
  if (connector.name === "apple") {
    return <Badge variant="secondary">Coming soon</Badge>;
  }

  // MusicBrainz — public API, no auth; show "Available" instead of "Connected"
  if (connector.name === "musicbrainz" && connector.connected) {
    return (
      <Badge className="bg-status-available/20 text-status-available border-status-available/30">
        Available
      </Badge>
    );
  }

  if (!connector.connected) {
    return <Badge variant="secondary">Not configured</Badge>;
  }

  // Check if token is expired
  if (connector.token_expires_at) {
    const isExpired = connector.token_expires_at * 1000 < Date.now();
    if (isExpired) {
      return (
        <Badge className="bg-status-expired/20 text-status-expired border-status-expired/30">
          Expired
        </Badge>
      );
    }
  }

  return (
    <Badge className="bg-status-connected/20 text-status-connected border-status-connected/30">
      Connected
    </Badge>
  );
}

export function ConnectorCard({
  connector,
}: {
  connector: ConnectorStatusSchema;
}) {
  const desc = descriptions[connector.name];
  const descText = connector.connected ? desc?.connected : desc?.disconnected;

  return (
    <Card className="p-4 space-y-1.5">
      <div className="flex items-center gap-3">
        <ConnectorIcon name={connector.name} className="text-sm" />

        {connector.connected && connector.account_name && (
          <span className="text-sm text-text-muted">
            {connector.account_name}
          </span>
        )}

        <span className="ml-auto">{getStatusBadge(connector)}</span>
      </div>

      {descText && <p className="text-xs text-text-faint">{descText}</p>}
    </Card>
  );
}
