/** Shared connector helpers (non-branding).
 *
 * Per-connector branding (logos, colors, button classes, UX descriptions)
 * lives in ``lib/connector-brand.tsx``. This module carries copy keyed on
 * backend-emitted *auth error codes*, the connect-strategy table keyed on
 * `auth_method`, and the token-connect instructions — the one table keyed on
 * connector name, because those steps are the provider's own.
 */

import type { ConnectorAuthMethod } from "#/api/generated/model";

/**
 * Which connect flow a card runs for an auth method.
 *
 * - `oauth` — fetch an auth URL and hand the browser to the provider.
 * - `token` — open a BYO-token form; the credential is PUT directly.
 * - `browser_bridge` — connect in-app through a provider SDK, no navigation.
 * - `none` — the user cannot connect it (public API, or not shipped yet).
 */
export type ConnectStrategyKind = "oauth" | "token" | "browser_bridge" | "none";

export interface ConnectStrategy {
  kind: ConnectStrategyKind;
}

/**
 * Connect flow per backend auth method. Keyed on the full `ConnectorAuthMethod`
 * union, so a new backend auth method is a compile error until the UI declares
 * how to connect it.
 *
 * `device_code` maps to `oauth`: no shipped connector declares it, and the
 * device-code entry point is the same auth-url fetch until a dedicated flow
 * exists.
 */
const connectStrategies: Record<ConnectorAuthMethod, ConnectStrategy> = {
  oauth: { kind: "oauth" },
  device_code: { kind: "oauth" },
  token: { kind: "token" },
  browser_bridge: { kind: "browser_bridge" },
  none: { kind: "none" },
  coming_soon: { kind: "none" },
};

/**
 * Connect flow descriptor for a connector's auth method. Wire data can carry a
 * method this build has no entry for; that connector is treated as one the user
 * cannot act on rather than crashing the card.
 */
export function connectStrategyFor(
  authMethod: ConnectorAuthMethod,
): ConnectStrategy {
  return connectStrategies[authMethod] ?? { kind: "none" };
}

/** Whether a connector can be connected by the user (stores a per-user
 * credential via some connect flow) — i.e. everything except "none"
 * (public API) and "coming_soon". */
export function isConnectable(authMethod: ConnectorAuthMethod): boolean {
  return connectStrategyFor(authMethod).kind !== "none";
}

/** Map auth callback reason codes to human-readable messages. */
const authErrorMessages: Record<string, string> = {
  access_denied: "You denied the authorization request",
  exchange_failed: "Token exchange failed — try again",
  invalid_state: "Session expired — please try again",
  no_token: "No authorization token received",
  not_configured: "API credentials not configured",
  no_session: "Failed to get session from Last.fm",
  no_session_key: "Failed to get session key from Last.fm",
  refresh_failed: "Session token could not be refreshed — please reconnect",
  scope_missing:
    "New permissions needed — reconnect to enable listening history",
  reauth_required: "Session expired — reconnect to continue",
};

/** Convert an auth error reason code to a human-readable string. */
export function humanizeAuthError(reason: string): string {
  return authErrorMessages[reason] ?? reason;
}

/**
 * One numbered instruction in a token-connect dialog.
 *
 * Markup-free so the table below stays data: the dialog renders `emphasis`
 * in bold and `link` as an external link, between `text` and `tail`.
 */
export interface TokenConnectStep {
  text: string;
  emphasis?: string;
  link?: { label: string; url: string };
  tail?: string;
}

/** Per-connector copy for the BYO-token connect dialog. */
export interface TokenConnectCopy {
  /** Why this connector uses a token; shown under the dialog title. */
  rationale: string;
  /** How to obtain the token. */
  steps: readonly TokenConnectStep[];
}

/**
 * Where each token-auth connector issues its personal access token.
 *
 * Keyed on connector name because the instructions are the provider's own —
 * the only per-connector copy the connect flow needs. Everything else in the
 * dialog comes from `display_name`.
 */
const tokenConnectCopy: Record<string, TokenConnectCopy> = {
  discogs: {
    rationale:
      "Discogs connects with a personal access token — your collection stays readable even while Mixd has no Discogs app of its own.",
    steps: [
      {
        text: "On discogs.com, open ",
        link: {
          label: "Settings → Developers",
          url: "https://www.discogs.com/settings/developers",
        },
        tail: ".",
      },
      {
        // Steers past the OAuth application fields on the same page, which
        // are not what the token flow needs.
        text: "In the ",
        emphasis: "Personal access token",
        tail: " section (ignore the OAuth application fields), choose Generate new token.",
      },
      { text: "Paste the token below." },
    ],
  },
};

/**
 * Token instructions for a connector, or undefined when this build ships
 * none — the dialog still offers a working token field.
 */
export function tokenConnectCopyFor(
  name: string,
): TokenConnectCopy | undefined {
  return tokenConnectCopy[name];
}
