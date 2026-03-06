import type { ConnectorStatusSchema } from "@/api/generated/model";
import { ConnectorIcon } from "@/components/shared/ConnectorIcon";
import { Badge } from "@/components/ui/badge";

/** Static descriptions — what each connector provides, regardless of connection state. */
const connectorDescriptions: Record<string, string> = {
  spotify: "Playlists, liked tracks, listening history",
  lastfm: "Scrobble counts, play history, loved tracks",
  musicbrainz: "Track identification, metadata enrichment",
  apple: "Library, playlists",
};

/** Auth/status detail — always rendered as the last line of each card. */
function getAuthDetail(connector: ConnectorStatusSchema): string {
  if (connector.name === "apple") {
    return "Coming soon \u00b7 connector under development";
  }
  if (connector.name === "musicbrainz") {
    return "Public API \u00b7 no authentication required";
  }
  if (!connector.connected) {
    return "Not connected \u00b7 run CLI to authenticate";
  }

  const identity = connector.account_name
    ? `Signed in as ${connector.account_name}`
    : "Signed in";

  if (connector.token_expires_at) {
    const isExpired = connector.token_expires_at * 1000 < Date.now();
    if (isExpired) {
      return `${identity} \u00b7 token expired`;
    }
    return `${identity} \u00b7 token refreshes automatically`;
  }

  return identity;
}

function getStatusBadge(connector: ConnectorStatusSchema) {
  if (connector.name === "apple") {
    return <Badge variant="secondary">Coming soon</Badge>;
  }

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
  const description = connectorDescriptions[connector.name] ?? "";
  const authDetail = getAuthDetail(connector);

  return (
    <div className="flex h-full flex-col rounded-xl border border-border bg-surface-elevated shadow-elevated p-4 transition-all duration-150 hover:shadow-glow hover:border-primary/20">
      <div className="flex items-start justify-between gap-2">
        <ConnectorIcon name={connector.name} iconSize="lg" />
        {getStatusBadge(connector)}
      </div>
      <p className="mt-2 text-sm text-text-muted">{description}</p>
      <p className="mt-auto pt-2 text-sm text-text-faint">{authDetail}</p>
    </div>
  );
}
