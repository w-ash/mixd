/** Shared connector copy (non-branding).
 *
 * Per-connector branding (logos, colors, button classes, UX descriptions)
 * lives in ``lib/connector-brand.tsx``. This module only carries copy
 * that's keyed on backend-emitted *auth error codes* rather than on
 * connector names.
 */

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
};

/** Convert an auth error reason code to a human-readable string. */
export function humanizeAuthError(reason: string): string {
  return authErrorMessages[reason] ?? reason;
}
