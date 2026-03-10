/** Sync status configuration — shared across playlist list and detail views. */

export const syncStatusConfig = {
  synced: { label: "Synced", dotClass: "bg-green-500" },
  syncing: { label: "Syncing\u2026", dotClass: "bg-blue-400 animate-pulse" },
  error: { label: "Sync failed", dotClass: "bg-red-500" },
  never_synced: { label: "Never synced", dotClass: "bg-text-muted/40" },
} as const;

export type SyncStatusKey = keyof typeof syncStatusConfig;

/** Get the status config for a sync status string. Falls back to never_synced for unknown values. */
export function getSyncStatusConfig(status: string) {
  return (
    syncStatusConfig[status as SyncStatusKey] ?? syncStatusConfig.never_synced
  );
}

/** Format last sync results as a compact string: "+12 added · -3 removed" */
export function formatSyncResults(
  added: number | null | undefined,
  removed: number | null | undefined,
): string | null {
  const parts: string[] = [];
  if (added != null && added > 0) parts.push(`+${added} added`);
  if (removed != null && removed > 0) parts.push(`-${removed} removed`);
  return parts.length > 0 ? parts.join(" · ") : null;
}
