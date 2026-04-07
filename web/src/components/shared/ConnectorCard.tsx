import { Loader2, Settings } from "lucide-react";
import { Collapsible } from "radix-ui";
import { useState } from "react";

import type { ConnectorStatusSchema } from "#/api/generated/model";
import { ConfirmationDialog } from "#/components/shared/ConfirmationDialog";
import {
  ConnectorIcon,
  getConnectorLabel,
} from "#/components/shared/ConnectorIcon";
import { Badge } from "#/components/ui/badge";
import { Button } from "#/components/ui/button";
import { useConnectorAuth } from "#/hooks/useConnectorAuth";
import {
  CONNECTABLE_SERVICES,
  connectButtonStyles,
  humanizeAuthError,
} from "#/lib/connectors";
import { cn } from "#/lib/utils";

/** Static descriptions — what each connector enables. */
const connectorDescriptions: Record<string, string> = {
  spotify: "Playlists, liked tracks, and library sync",
  lastfm: "Listening history, play counts, and loved tracks",
  musicbrainz: "Track metadata enrichment and identification",
  apple: "Playlists and library sync",
};

/** Left border accent for active integrations (static strings for Tailwind). */
const activeBorderClasses: Record<string, string> = {
  spotify: "border-l-spotify",
  lastfm: "border-l-lastfm",
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
  if (!connector.connected) {
    if (authError) return "error";
    return "disconnected";
  }
  // Connected — ignore stale auth errors from callback replays
  if (connector.token_expires_at) {
    const isExpired = connector.token_expires_at * 1000 < Date.now();
    if (isExpired) return "expired";
  }
  return "connected";
}

// ---------------------------------------------------------------------------
// Status line — the secondary text that appears below the connector name
// ---------------------------------------------------------------------------

function StatusLine({
  state,
  connector,
  authError,
}: {
  state: CardState;
  connector: ConnectorStatusSchema;
  authError?: string;
}) {
  switch (state) {
    case "connected":
      return (
        <span className="text-text-muted">
          {connector.account_name
            ? `Signed in as ${connector.account_name}`
            : "Signed in"}
          <span className="mx-1.5 text-border">·</span>
          <span className="text-text-faint">
            {connector.token_expires_at
              ? "Token refreshes automatically"
              : "Permanent session"}
          </span>
        </span>
      );
    case "expired":
      return (
        <span className="text-status-expired">
          {connector.account_name
            ? `${connector.account_name} — session expired`
            : "Session expired"}
        </span>
      );
    case "error":
      return (
        <span className="text-destructive">
          Connection failed
          {authError ? `: ${humanizeAuthError(authError)}` : ""}
        </span>
      );
    case "coming_soon":
    case "public_api":
    case "disconnected":
      return (
        <span className="text-text-faint">
          {connectorDescriptions[connector.name]}
        </span>
      );
  }
}

// ---------------------------------------------------------------------------
// Row action — the right-side element (button, badge, or gear)
// ---------------------------------------------------------------------------

function SettingsGear({
  label,
  showSettings,
}: {
  label: string;
  showSettings: boolean;
}) {
  return (
    <Collapsible.Trigger asChild>
      <Button
        variant="ghost"
        size="icon-xs"
        aria-label={`${label} settings`}
        className={cn(
          "text-text-faint transition-colors hover:text-text",
          showSettings && "text-text",
        )}
      >
        <Settings className="size-3.5" />
      </Button>
    </Collapsible.Trigger>
  );
}

function RowAction({
  state,
  label,
  connectorName,
  connect,
  isConnecting,
  hasSettings,
  showSettings,
}: {
  state: CardState;
  label: string;
  connectorName: string;
  connect: () => void;
  isConnecting: boolean;
  hasSettings: boolean;
  showSettings: boolean;
}) {
  const gear = hasSettings && (
    <SettingsGear label={label} showSettings={showSettings} />
  );

  switch (state) {
    case "coming_soon":
      return <Badge variant="secondary">Coming soon</Badge>;
    case "public_api":
      return (
        <Badge className="bg-status-available/20 text-status-available border-status-available/30">
          Available
        </Badge>
      );
    case "connected":
      return (
        <div className="flex items-center gap-1.5">
          <span
            className="size-2 rounded-full bg-status-connected"
            aria-hidden="true"
          />
          {gear}
        </div>
      );
    case "expired":
      return (
        <div className="flex items-center gap-2">
          {gear}
          <Button
            onClick={connect}
            disabled={isConnecting}
            className={connectButtonStyles[connectorName] ?? ""}
            size="xs"
          >
            {isConnecting && <Loader2 className="mr-1 size-3 animate-spin" />}
            Reconnect
          </Button>
        </div>
      );
    case "error":
    case "disconnected":
      return (
        <Button
          onClick={connect}
          disabled={isConnecting}
          className={cn(
            "min-h-[36px]",
            connectButtonStyles[connectorName] ?? "",
          )}
          size="xs"
        >
          {isConnecting && <Loader2 className="mr-1 size-3 animate-spin" />}
          {state === "error" ? "Try again" : `Connect ${label}`}
        </Button>
      );
  }
}

// ---------------------------------------------------------------------------
// ConnectorCard — single-row layout for the integrations page
// ---------------------------------------------------------------------------

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
  const [showSettings, setShowSettings] = useState(false);
  const [showDisconnect, setShowDisconnect] = useState(false);

  const label = getConnectorLabel(connector.name);
  const isActive = state === "connected" || state === "expired";
  const isMuted = state === "coming_soon";
  const hasSettings = isConnectable && isActive;

  return (
    <>
      <Collapsible.Root open={showSettings} onOpenChange={setShowSettings}>
        <div
          className={cn(
            "border-l-2 border-l-transparent px-4 py-3 transition-colors",
            isActive && activeBorderClasses[connector.name],
            isMuted && "opacity-50",
          )}
        >
          {/* Main row */}
          <div className="flex items-center gap-3">
            <ConnectorIcon name={connector.name} labelHidden />
            <div className="min-w-0 flex-1">
              <span className="font-display text-sm font-medium text-text">
                {label}
              </span>
              <p className="mt-0.5 truncate text-xs">
                <StatusLine
                  state={state}
                  connector={connector}
                  authError={authError}
                />
              </p>
            </div>
            <RowAction
              state={state}
              label={label}
              connectorName={connector.name}
              connect={connect}
              isConnecting={isConnecting}
              hasSettings={hasSettings}
              showSettings={showSettings}
            />
          </div>

          {/* Settings panel — slides open below the row */}
          <Collapsible.Content className="overflow-hidden data-[state=closed]:animate-collapsible-up data-[state=open]:animate-collapsible-down">
            <div className="ml-8 mt-2 border-t border-border pt-2">
              <Button
                variant="ghost"
                size="xs"
                className="text-text-faint hover:text-destructive"
                onClick={() => setShowDisconnect(true)}
              >
                Disconnect {label}
              </Button>
            </div>
          </Collapsible.Content>
        </div>
      </Collapsible.Root>

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
            setShowSettings(false);
          }}
        />
      )}
    </>
  );
}
