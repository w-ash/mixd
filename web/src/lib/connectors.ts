/** Shared connector helpers (non-branding).
 *
 * Per-connector branding (logos, colors, button classes, UX descriptions)
 * lives in ``lib/connector-brand.tsx``. This module carries copy keyed on
 * backend-emitted *auth error codes* and the connect-capability predicate —
 * nothing keyed on connector names.
 */

import type { ConnectorAuthMethod } from "#/api/generated/model";

/** Auth methods a user can act on to connect the service. */
const connectableAuthMethods: ReadonlySet<ConnectorAuthMethod> = new Set([
  "oauth",
  "browser_bridge",
  "token",
  "device_code",
]);

/** Whether a connector can be connected by the user (stores a per-user
 * credential via some connect flow) — i.e. everything except "none"
 * (public API) and "coming_soon". */
export function isConnectable(authMethod: ConnectorAuthMethod): boolean {
  return connectableAuthMethods.has(authMethod);
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
  authorize_failed: "Authorization was cancelled or failed — try again",
};

/** Convert an auth error reason code to a human-readable string. */
export function humanizeAuthError(reason: string): string {
  return authErrorMessages[reason] ?? reason;
}
