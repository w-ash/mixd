import { Loader2, Settings } from "lucide-react";
import { Collapsible } from "radix-ui";
import { type ComponentType, useState } from "react";

import type { ConnectorMetadataSchema } from "#/api/generated/model";
import { ConfirmationDialog } from "#/components/shared/ConfirmationDialog";
import { ConnectorIcon } from "#/components/shared/ConnectorIcon";
import { TokenConnectDialog } from "#/components/shared/TokenConnectDialog";
import { Badge } from "#/components/ui/badge";
import { Button } from "#/components/ui/button";
import { useAppleMusicConnect } from "#/hooks/useAppleMusicConnect";
import { useConnectorAuth } from "#/hooks/useConnectorAuth";
import { type ConnectorBrand, connectorBrand } from "#/lib/connector-brand";
import {
  type ConnectStrategyKind,
  connectStrategyFor,
  humanizeAuthError,
  isConnectable,
} from "#/lib/connectors";
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
      // Connectors with a generic status suffix (e.g. Discogs' collection
      // count) render it in place of the signed-in/freshness copy — there's
      // often no account_name for a token-auth connector, and the detail is
      // the more useful thing to show.
      if (connector.detail) {
        return (
          <span className="text-text-muted">{`connected · ${connector.detail}`}</span>
        );
      }
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
    case "needs_reauth": {
      // scope_missing: session still works — the stored grant just predates
      // a scope the app now requests (e.g. listening history). One-time
      // re-consent, not a session problem.
      // reauth_required: the session itself aged out or was revoked
      // (Spotify's 6-month refresh grant, Apple's Music User Token) — a
      // different situation from a permissions gap, so it gets its own copy.
      const sessionExpired = connector.auth_error === "reauth_required";
      const suffix = sessionExpired
        ? "session expired, reconnect"
        : "new permissions needed";
      return (
        <span className="text-status-expired">
          {connector.account_name
            ? `${connector.account_name} — ${suffix}`
            : sessionExpired
              ? "Session expired, reconnect"
              : "New permissions needed"}
        </span>
      );
    }
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

interface ConnectActionProps {
  connector: ConnectorMetadataSchema;
  /** Button copy for the card's current state. */
  label: string;
  className: string;
}

function ConnectButton({
  label,
  className,
  isConnecting,
  onClick,
}: {
  label: string;
  className: string;
  isConnecting: boolean;
  onClick: () => void;
}) {
  return (
    <Button
      onClick={onClick}
      disabled={isConnecting}
      className={className}
      size="xs"
    >
      {isConnecting && <Loader2 className="mr-1 size-3 animate-spin" />}
      {label}
    </Button>
  );
}

/** Fetch an auth URL and hand the browser to the provider. */
function OAuthConnectAction({
  connector,
  label,
  className,
}: ConnectActionProps) {
  const { connect, isConnecting } = useConnectorAuth(
    connector.name,
    connector.display_name,
  );
  return (
    <ConnectButton
      label={label}
      className={className}
      isConnecting={isConnecting}
      onClick={connect}
    />
  );
}

/** Open the BYO-token form instead of redirecting anywhere. */
function TokenConnectAction({
  connector,
  label,
  className,
}: ConnectActionProps) {
  const [showForm, setShowForm] = useState(false);
  return (
    <>
      <ConnectButton
        label={label}
        className={className}
        isConnecting={false}
        onClick={() => setShowForm(true)}
      />
      <TokenConnectDialog
        service={connector.name}
        displayName={connector.display_name}
        open={showForm}
        onOpenChange={setShowForm}
      />
    </>
  );
}

/**
 * Connect in-app through a provider SDK, with no navigation.
 *
 * `useAppleMusicConnect` is Apple-specific — Apple Music is the only
 * `browser_bridge` connector, and its token POST is a documented special
 * case. Mounting the hook here rather than in the card also makes the
 * prewarm structural: this component exists only while the card offers a
 * connect action, which is exactly when the setup is worth running early.
 */
function BridgeConnectAction({ label, className }: ConnectActionProps) {
  const { connect, isConnecting } = useAppleMusicConnect({ prewarm: true });
  return (
    <ConnectButton
      label={label}
      className={className}
      isConnecting={isConnecting}
      onClick={connect}
    />
  );
}

/**
 * Connect flow per strategy kind.
 *
 * Each flow owns state a sibling has no use for — a mutation, a redirect
 * flag, a dialog — and hooks cannot be called conditionally, so the choice
 * is made by mounting one component rather than by composing all three in
 * one hook. React's own guidance for state behind a condition: extract a
 * component. Module-level definitions keep the identity stable, so switching
 * card state remounts only when the kind actually changes.
 */
const connectActions: Record<
  ConnectStrategyKind,
  ComponentType<ConnectActionProps> | null
> = {
  oauth: OAuthConnectAction,
  token: TokenConnectAction,
  browser_bridge: BridgeConnectAction,
  none: null,
};

function ConnectAction(props: ConnectActionProps) {
  const Action =
    connectActions[connectStrategyFor(props.connector.auth_method).kind];
  return Action ? <Action {...props} /> : null;
}

function RowAction({
  state,
  connector,
  brand,
  hasSettings,
  showSettings,
}: {
  state: CardState;
  connector: ConnectorMetadataSchema;
  brand: ConnectorBrand | undefined;
  hasSettings: boolean;
  showSettings: boolean;
}) {
  const label = connector.display_name;
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
    case "needs_reauth":
      return (
        <div className="flex items-center gap-2">
          {gear}
          <ConnectAction
            connector={connector}
            label="Reconnect"
            className={brand?.buttonClass ?? ""}
          />
        </div>
      );
    case "error":
    case "disconnected":
      return (
        <ConnectAction
          connector={connector}
          label={state === "error" ? "Try again" : `Connect ${label}`}
          className={cn("min-h-[36px]", brand?.buttonClass ?? "")}
        />
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
  // Connect-capable methods store a disconnectable per-user credential via
  // some connect flow (oauth redirect, MusicKit bridge, token, device code).
  const connectable = isConnectable(connector.auth_method);
  // Connecting belongs to the per-kind action below; the card owns only the
  // removal, which is the same DELETE for every connect flow.
  const { disconnect, isDisconnecting } = useConnectorAuth(
    connector.name,
    connector.display_name,
  );
  const [showSettings, setShowSettings] = useState(false);
  const [showDisconnect, setShowDisconnect] = useState(false);

  const brand = connectorBrand[connector.name];
  const label = connector.display_name;
  const isActive =
    state === "connected" || state === "expired" || state === "needs_reauth";
  const isMuted = state === "coming_soon";
  const hasSettings = connectable && isActive;

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
              connector={connector}
              brand={brand}
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
      {connectable && (
        <ConfirmationDialog
          open={showDisconnect}
          onOpenChange={setShowDisconnect}
          title={`Disconnect ${label}?`}
          description="Your credentials are removed. Imported likes, plays, playlists, and mappings stay — syncing stops until you reconnect."
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
