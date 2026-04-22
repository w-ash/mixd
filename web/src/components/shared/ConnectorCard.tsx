import { Loader2, Settings } from "lucide-react";
import { Collapsible } from "radix-ui";
import { useState } from "react";

import type { ConnectorMetadataSchema } from "#/api/generated/model";
import { ConfirmationDialog } from "#/components/shared/ConfirmationDialog";
import { ConnectorIcon } from "#/components/shared/ConnectorIcon";
import { Badge } from "#/components/ui/badge";
import { Button } from "#/components/ui/button";
import { useConnectorAuth } from "#/hooks/useConnectorAuth";
import { type ConnectorBrand, connectorBrand } from "#/lib/connector-brand";
import { humanizeAuthError } from "#/lib/connectors";
import { formatRelativeTime } from "#/lib/format";
import { cn } from "#/lib/utils";

/** The backend's ``ConnectorMetadataSchemaStatus`` literal union. */
type CardState = ConnectorMetadataSchema["status"];

function StatusLine({
  state,
  connector,
  brand,
  authError,
}: {
  state: CardState;
  connector: ConnectorMetadataSchema;
  brand: ConnectorBrand | undefined;
  authError?: string;
}) {
  switch (state) {
    case "connected": {
      const freshness = connector.last_synced_at
        ? `Synced ${formatRelativeTime(connector.last_synced_at)}`
        : connector.token_expires_at
          ? "Token refreshes automatically"
          : "Permanent session";
      return (
        <span className="text-text-muted">
          {connector.account_name
            ? `Signed in as ${connector.account_name}`
            : "Signed in"}
          <span className="mx-1.5 text-border">·</span>
          <span className="text-text-faint">{freshness}</span>
        </span>
      );
    }
    case "expired":
      return (
        <span className="text-status-expired">
          {connector.account_name
            ? `${connector.account_name} — session expired`
            : "Session expired"}
        </span>
      );
    case "error": {
      // Backend-observed auth errors win over transient callback-URL errors.
      const reason = connector.auth_error ?? authError;
      return (
        <span className="text-destructive">
          Connection failed
          {reason ? `: ${humanizeAuthError(reason)}` : ""}
        </span>
      );
    }
    case "coming_soon":
    case "public_api":
    case "disconnected":
      return (
        <span className="text-text-faint">{brand?.description ?? ""}</span>
      );
  }
}

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
  brand,
  connect,
  isConnecting,
  hasSettings,
  showSettings,
}: {
  state: CardState;
  label: string;
  brand: ConnectorBrand | undefined;
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
            className={brand?.buttonClass ?? ""}
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
          className={cn("min-h-[36px]", brand?.buttonClass ?? "")}
          size="xs"
        >
          {isConnecting && <Loader2 className="mr-1 size-3 animate-spin" />}
          {state === "error" ? "Try again" : `Connect ${label}`}
        </Button>
      );
  }
}

interface ConnectorCardProps {
  connector: ConnectorMetadataSchema;
  /** Error reason from auth callback — triggers error state on the card. */
  authError?: string;
}

export function ConnectorCard({ connector, authError }: ConnectorCardProps) {
  const state: CardState =
    authError && !connector.connected ? "error" : connector.status;
  const isConnectable = connector.auth_method === "oauth";
  const { connect, disconnect, isConnecting, isDisconnecting } =
    useConnectorAuth(connector.name, connector.display_name);
  const [showSettings, setShowSettings] = useState(false);
  const [showDisconnect, setShowDisconnect] = useState(false);

  const brand = connectorBrand[connector.name];
  const label = connector.display_name;
  const isActive = state === "connected" || state === "expired";
  const isMuted = state === "coming_soon";
  const hasSettings = isConnectable && isActive;

  return (
    <>
      <Collapsible.Root open={showSettings} onOpenChange={setShowSettings}>
        <div
          className={cn(
            "border-l-2 border-l-transparent px-4 py-3 transition-colors",
            isActive && brand?.borderColor,
            isMuted && "opacity-50",
          )}
        >
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
                  brand={brand}
                  authError={authError}
                />
              </p>
            </div>
            <RowAction
              state={state}
              label={label}
              brand={brand}
              connect={connect}
              isConnecting={isConnecting}
              hasSettings={hasSettings}
              showSettings={showSettings}
            />
          </div>

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
