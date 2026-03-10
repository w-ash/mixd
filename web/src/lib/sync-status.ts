/** Sync status configuration — shared across playlist list and detail views. */

export const syncStatusConfig = {
  synced: { label: "Synced", dotClass: "bg-green-500" },
  syncing: { label: "Syncing", dotClass: "bg-blue-400 animate-pulse" },
  error: { label: "Error", dotClass: "bg-red-500" },
  never_synced: { label: "Never synced", dotClass: "bg-text-muted/40" },
} as const;

export type SyncStatusKey = keyof typeof syncStatusConfig;

/** Get the status config for a sync status string. Falls back to never_synced for unknown values. */
export function getSyncStatusConfig(status: string) {
  return (
    syncStatusConfig[status as SyncStatusKey] ?? syncStatusConfig.never_synced
  );
}
