/** Shared connector constants used across pages and components. */

/** Services that support OAuth connect/disconnect flows. */
export const CONNECTABLE_SERVICES = new Set(["spotify", "lastfm"]);

/** Connector-colored button classes (static strings for Tailwind scanning). */
export const connectButtonStyles: Record<string, string> = {
  spotify:
    "bg-spotify/15 text-spotify border border-spotify/30 hover:bg-spotify/25",
  lastfm: "bg-lastfm/15 text-lastfm border border-lastfm/30 hover:bg-lastfm/25",
};

/** Map auth callback reason codes to human-readable messages. */
const authErrorMessages: Record<string, string> = {
  access_denied: "You denied the authorization request",
  exchange_failed: "Token exchange failed — try again",
  invalid_state: "Session expired — please try again",
  no_token: "No authorization token received",
  not_configured: "API credentials not configured",
  no_session: "Failed to get session from Last.fm",
  no_session_key: "Failed to get session key from Last.fm",
};

/** Convert an auth error reason code to a human-readable string. */
export function humanizeAuthError(reason: string): string {
  return authErrorMessages[reason] ?? reason;
}
