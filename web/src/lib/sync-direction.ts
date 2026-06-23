/**
 * Sync-direction vocabulary — one client-side source of truth, mirroring the
 * backend's `direction_label` (src/interface/api/schemas/playlists.py).
 *
 * Our sync is a one-way *replace*, not a two-way merge: each direction names
 * the side that gets overwritten. The API hands back `direction_label` for the
 * current direction; the interactive chooser needs both, so the formatting
 * lives here and the read-only indicator prefers the API value when present.
 */

export type SyncDirection = "push" | "pull";

/** "Mixd → Spotify (replaces Spotify)" / "Spotify → Mixd (replaces Mixd)". */
export function formatDirectionLabel(
  direction: SyncDirection,
  connectorLabel: string,
): string {
  return direction === "push"
    ? `Mixd → ${connectorLabel} (replaces ${connectorLabel})`
    : `${connectorLabel} → Mixd (replaces Mixd)`;
}

/** One-line plain-language description: which side is the source of truth. */
export function describeDirection(
  direction: SyncDirection,
  connectorLabel: string,
): string {
  return direction === "push"
    ? `Mixd is the source of truth — ${connectorLabel} is replaced to match.`
    : `${connectorLabel} is the source of truth — Mixd is replaced to match.`;
}
