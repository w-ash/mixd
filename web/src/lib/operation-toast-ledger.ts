/**
 * Cross-component "who already toasted this run" ledger.
 *
 * A finished import/sync can be announced by two independent surfaces: the
 * foreground card watching it live (success/partial toast) and the global
 * operations provider catching its terminal status on a poll (failure toast).
 * Whoever claims a run_id first owns its toast; the other backs off — so a run
 * is never announced twice.
 *
 * Module-level (one ledger per tab). Entries are never evicted: a run reaches a
 * terminal state exactly once, and the set stays tiny over a session.
 */

const toastedRunIds = new Set<string>();

/** Claim the right to toast for `runId`. Returns true for the first caller. */
export function claimRunToast(runId: string): boolean {
  if (toastedRunIds.has(runId)) return false;
  toastedRunIds.add(runId);
  return true;
}

/** Test-only reset. */
export function __resetRunToastLedger(): void {
  toastedRunIds.clear();
}
