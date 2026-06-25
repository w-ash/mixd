/**
 * Global import/sync operations awareness (headless watcher).
 *
 * Poll-based terminal surfacing: diff the `?status=running` list across polls;
 * when a run leaves the running set, fetch its terminal status and announce it
 * via the shared run-completed toast — even if the user navigated away. The
 * toast ledger dedups against any foreground card watching the same run, so a
 * run is announced exactly once. Failed import runs offer a "Retry failed only"
 * action (the server says which runs are `retryable`).
 *
 * Mounted INSIDE the router (it needs `useNavigate` for the toast action),
 * wrapping `<Routes>`. The sidebar badge reads `useActiveOperations()` directly
 * off the same shared cache. This component renders no UI and exposes no
 * context — it is purely the background announcer.
 *
 * Polling is gated on auth so it never runs on the login page (the provider
 * mounts above the route-level AuthGuard). `useAuthenticate` only works inside
 * the Neon provider — which `AuthProvider` omits when auth is disabled — so we
 * branch on the build-time `authEnabled` constant (stable per build → no
 * rules-of-hooks violation).
 */

import { useAuthenticate } from "@neondatabase/auth/react/ui";
import { type ReactNode, useCallback, useEffect, useRef } from "react";
import { useNavigate } from "react-router";

import { authEnabled } from "#/api/auth";
import {
  getOperationRunApiV1OperationRunsRunIdGet,
  retryFailedOperationApiV1OperationRunsRunIdRetryFailedPost,
} from "#/api/generated/operation-runs/operation-runs";
import { useActiveOperations } from "#/hooks/useActiveOperations";
import { claimRunToast } from "#/lib/operation-toast-ledger";
import { toasts } from "#/lib/toasts";

export function OperationsProvider({ children }: { children: ReactNode }) {
  return authEnabled ? (
    <AuthGatedWatcher>{children}</AuthGatedWatcher>
  ) : (
    <OperationsWatcher isAuthed>{children}</OperationsWatcher>
  );
}

/** Reads Neon auth so polling pauses on the login page. Only mounted when
 *  `authEnabled` (so the Neon provider context exists). */
function AuthGatedWatcher({ children }: { children: ReactNode }) {
  const { data: session } = useAuthenticate();
  return (
    <OperationsWatcher isAuthed={Boolean(session)}>
      {children}
    </OperationsWatcher>
  );
}

function OperationsWatcher({
  isAuthed,
  children,
}: {
  isAuthed: boolean;
  children: ReactNode;
}) {
  const navigate = useNavigate();
  const { data: activeOperations = [] } = useActiveOperations(isAuthed);

  // Re-run only the failed items, then surface the result on the next poll.
  // Falls back to the run's log when the server says nothing is retryable (409).
  const retryFailed = useCallback(
    async (runId: string) => {
      try {
        const resp =
          await retryFailedOperationApiV1OperationRunsRunIdRetryFailedPost(
            runId,
          );
        if (resp.status === 202) {
          toasts.info("Retrying failed items…");
          return;
        }
      } catch {
        // fall through to the log
      }
      navigate(`/settings/imports?run=${runId}`);
    },
    [navigate],
  );

  const surfaceTerminal = useCallback(
    async (runId: string) => {
      // customFetch throws ApiError on any non-2xx; treat a transient failure
      // (network/5xx/404 race) as "nothing to surface" and let the next poll
      // retry rather than leak an unhandled rejection.
      const resp = await getOperationRunApiV1OperationRunsRunIdGet(runId).catch(
        () => null,
      );
      if (resp?.status !== 200) return;
      const run = resp.data;
      // Cancelled/superseded runs are a neutral, deliberate stop — don't toast.
      if (run.status === "cancelled") return;
      // Shared ledger: skip if a foreground card already announced this run.
      if (!claimRunToast(runId)) return;

      toasts.runCompleted({
        operationType: run.operation_type,
        counts: run.counts,
        issueCount: run.issues.length,
        runId,
        failed: run.status === "error",
        onNavigate: navigate,
        action: run.retryable
          ? { label: "Retry failed only", onClick: () => retryFailed(runId) }
          : undefined,
      });
    },
    [navigate, retryFailed],
  );

  // Diff the running set across polls. A run that *left* the set → resolve its
  // terminal status. Seeded on the first poll so a pre-existing terminal run
  // (predating mount) never retro-toasts.
  const prevRunningRef = useRef<Set<string> | null>(null);
  const resolvedRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    const running = new Set(activeOperations.map((r) => r.id));
    const prev = prevRunningRef.current;
    prevRunningRef.current = running;
    if (prev === null) return; // first poll: seed only

    for (const id of prev) {
      if (running.has(id) || resolvedRef.current.has(id)) continue;
      resolvedRef.current.add(id);
      void surfaceTerminal(id);
    }
  }, [activeOperations, surfaceTerminal]);

  return <>{children}</>;
}
