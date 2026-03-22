import { Loader2 } from "lucide-react";
import { useState } from "react";

import type { ConnectorStatusSchema } from "@/api/generated/model";
import { ConfirmationDialog } from "@/components/shared/ConfirmationDialog";
import {
  ConnectorIcon,
  getConnectorLabel,
} from "@/components/shared/ConnectorIcon";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useConnectorAuth } from "@/hooks/useConnectorAuth";
import {
  CONNECTABLE_SERVICES,
  connectButtonStyles,
  humanizeAuthError,
} from "@/lib/connectors";
import { cn } from "@/lib/utils";

/** Static descriptions — what each connector enables. */
const connectorDescriptions: Record<string, string> = {
  spotify: "Playlists, liked tracks, and library sync",
  lastfm: "Listening history, play counts, and loved tracks",
  musicbrainz: "Track metadata enrichment and identification",
  apple: "Playlists and library sync (coming soon)",
};

/** Permissions transparency — shown below the connect button. */
const permissionTexts: Record<string, string> = {
  spotify:
    "We'll access your playlists, liked tracks, and library. We never modify without your explicit action.",
  lastfm: "We'll access your listening history and loved tracks.",
};

/** Left border accent for connected cards (static strings for Tailwind). */
const connectorBorderClasses: Record<string, string> = {
  spotify: "border-l-2 border-l-spotify",
  lastfm: "border-l-2 border-l-lastfm",
};

type CardState =
  | "coming_soon"
  | "public_api"
  | "disconnected"
  | "connected"
  | "expired"
  | "error";

function getCardState(
  connector: ConnectorStatusSchema,
  authError?: string,
): CardState {
  if (connector.name === "apple") return "coming_soon";
  if (connector.name === "musicbrainz") return "public_api";
  if (authError) return "error";
  if (!connector.connected) return "disconnected";
  if (connector.token_expires_at) {
    const isExpired = connector.token_expires_at * 1000 < Date.now();
    if (isExpired) return "expired";
  }
  return "connected";
}

function StatusBadge({ state }: { state: CardState }) {
  switch (state) {
    case "coming_soon":
      return <Badge variant="secondary">Coming soon</Badge>;
    case "public_api":
      return (
        <Badge className="bg-status-available/20 text-status-available border-status-available/30">
          Available
        </Badge>
      );
    case "disconnected":
    case "error":
      return <Badge variant="secondary">Not configured</Badge>;
    case "expired":
      return (
        <Badge className="bg-status-expired/20 text-status-expired border-status-expired/30">
          Session expired
        </Badge>
      );
    case "connected":
      return (
        <Badge className="bg-status-connected/20 text-status-connected border-status-connected/30">
          Connected
        </Badge>
      );
  }
}

interface ConnectorCardProps {
  connector: ConnectorStatusSchema;
  /** Error reason from auth callback — triggers error state on the card. */
  authError?: string;
}

export function ConnectorCard({ connector, authError }: ConnectorCardProps) {
  const state = getCardState(connector, authError);
  const isConnectable = CONNECTABLE_SERVICES.has(connector.name);
  const { connect, disconnect, isConnecting, isDisconnecting } =
    useConnectorAuth(connector.name);
  const [showDisconnect, setShowDisconnect] = useState(false);

  const label = getConnectorLabel(connector.name);
  const description = connectorDescriptions[connector.name] ?? "";
  const permission = permissionTexts[connector.name];
  const isActive = state === "connected" || state === "expired";
  const isMuted = state === "coming_soon";

  return (
    <>
      <div
        className={cn(
          "flex h-full flex-col rounded-xl border border-border bg-surface-elevated p-5 transition-all duration-150",
          isActive &&
            cn(
              "shadow-elevated hover:shadow-glow hover:border-primary/20",
              connectorBorderClasses[connector.name],
            ),
          isMuted && "opacity-60",
        )}
      >
        {/* Header: icon + badge */}
        <div className="flex items-start justify-between gap-2">
          <ConnectorIcon name={connector.name} iconSize="lg" />
          <StatusBadge state={state} />
        </div>

        {/* Description */}
        <p className="mt-2 text-sm text-text-muted">{description}</p>

        {/* State-specific content */}
        <div className="mt-auto pt-3">
          {state === "coming_soon" && (
            <p className="text-sm text-text-faint">
              Connector under development
            </p>
          )}

          {state === "public_api" && (
            <p className="text-sm text-text-faint">
              Public API · no authentication required
            </p>
          )}

          {state === "connected" && (
            <div className="space-y-1">
              <p className="text-sm text-text">
                <span
                  className="mr-1.5 inline-block size-2 rounded-full bg-status-connected align-middle"
                  aria-hidden="true"
                />
                {connector.account_name
                  ? `Signed in as ${connector.account_name}`
                  : "Signed in"}
              </p>
              <div className="flex items-center justify-between">
                <p className="text-xs text-text-faint">
                  {connector.token_expires_at
                    ? "Token refreshes automatically"
                    : "Permanent session"}
                </p>
                <button
                  type="button"
                  onClick={() => setShowDisconnect(true)}
                  aria-label={`Disconnect ${label}`}
                  className="rounded-sm text-xs text-text-faint transition-colors hover:text-destructive focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-surface-elevated"
                >
                  Disconnect
                </button>
              </div>
            </div>
          )}

          {state === "expired" && (
            <div className="space-y-2">
              <p className="text-sm text-text">
                <span
                  className="mr-1.5 inline-block size-2 rounded-full bg-status-expired align-middle"
                  aria-hidden="true"
                />
                {connector.account_name
                  ? `Signed in as ${connector.account_name}`
                  : "Session expired"}
              </p>
              <Button
                onClick={connect}
                disabled={isConnecting}
                className={connectButtonStyles[connector.name] ?? ""}
                size="sm"
              >
                {isConnecting && (
                  <Loader2 className="mr-1.5 size-3.5 animate-spin" />
                )}
                Reconnect
              </Button>
            </div>
          )}

          {state === "error" && (
            <div className="space-y-2">
              <p className="text-sm text-destructive">
                Connection failed
                {authError ? `: ${humanizeAuthError(authError)}` : ""}
              </p>
              <Button
                onClick={connect}
                disabled={isConnecting}
                className={connectButtonStyles[connector.name] ?? ""}
                size="sm"
              >
                {isConnecting && (
                  <Loader2 className="mr-1.5 size-3.5 animate-spin" />
                )}
                Try again
              </Button>
            </div>
          )}

          {state === "disconnected" && isConnectable && (
            <div className="space-y-2">
              <Button
                onClick={connect}
                disabled={isConnecting}
                className={cn(
                  "w-full sm:w-auto min-h-[44px]",
                  connectButtonStyles[connector.name] ?? "",
                )}
                size="sm"
              >
                {isConnecting && (
                  <Loader2 className="mr-1.5 size-3.5 animate-spin" />
                )}
                Connect {label}
              </Button>
              {permission && (
                <p className="text-xs leading-relaxed text-text-faint">
                  {permission}
                </p>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Disconnect confirmation dialog */}
      {isConnectable && (
        <ConfirmationDialog
          open={showDisconnect}
          onOpenChange={setShowDisconnect}
          title={`Disconnect ${label}?`}
          description="Your playlists and sync settings will be preserved, but imports and syncing will stop until you reconnect."
          confirmLabel="Disconnect"
          destructive
          isPending={isDisconnecting}
          onConfirm={() => {
            disconnect();
            setShowDisconnect(false);
          }}
        />
      )}
    </>
  );
}
