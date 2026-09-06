/**
 * Announce a long-running operation exactly once, when it concludes.
 *
 * Every OperationRun-backed surface that watches its own operation over SSE
 * needs the same four guards, and getting any of them wrong is visible to the
 * user:
 *
 * - **Once per operation.** A ref keyed on `operationId` survives the
 *   re-renders that keep handing back the same terminal progress object.
 * - **Once per run, across surfaces.** The global operations watcher polls the
 *   same run's audit row and announces it too. `claimRunToast` is
 *   first-writer-wins, so whichever surface gets there first owns the toast.
 * - **Cancelled is not news.** A deliberate stop draws no toast, matching the
 *   global watcher.
 * - **Partial is failure-adjacent, not success.** A run that streams as
 *   `completed` carrying an `errors` count earns the warning styling and the
 *   "View log" action — see `issueCountFromCounts`.
 *
 * The toast is an {@link useEffectEvent} so the Effect depends only on the
 * terminal transition, not on the progress object or the callbacks.
 */

import { useEffect, useEffectEvent, useRef } from "react";
import { useNavigate } from "react-router";

import {
  isTerminalProgress,
  type OperationProgress,
} from "#/hooks/useOperationProgress";
import { claimRunToast } from "#/lib/operation-toast-ledger";
import { issueCountFromCounts, toasts } from "#/lib/toasts";

export interface RunToastContext {
  operationId: string;
  runId: string | null;
  /** The terminal progress state. */
  progress: OperationProgress;
  /** True unless the run reached `completed`. */
  failed: boolean;
  /** Items the run could not process. */
  issueCount: number;
  navigate: (path: string) => void;
}

export interface UseRunCompletedToastOptions {
  operationId: string | null;
  /** Audit-row id, when one was persisted. Null disables the ledger claim and
   *  the "View log" deep link. */
  runId: string | null;
  progress: OperationProgress | null;
  /** Selects the toast title and count phrasing — see `toasts.runCompleted`. */
  operationType: string;
  /** Replaces the default `toasts.runCompleted` call, for surfaces that
   *  summarise their own sub-operations. Skipped when another surface won the
   *  ledger claim. */
  buildToast?: (context: RunToastContext) => void;
  /** Runs once the operation concludes, whoever won the toast — for the
   *  refresh/cleanup work that must happen either way. */
  onTerminal?: (context: RunToastContext) => void;
}

export function useRunCompletedToast({
  operationId,
  runId,
  progress,
  operationType,
  buildToast,
  onTerminal,
}: UseRunCompletedToastOptions): void {
  const navigate = useNavigate();
  const announcedOpIdRef = useRef<string | null>(null);

  const announce = useEffectEvent(() => {
    if (operationId === null || progress === null) return;
    if (announcedOpIdRef.current === operationId) return;
    announcedOpIdRef.current = operationId;

    const context: RunToastContext = {
      operationId,
      runId,
      progress,
      failed: progress.status !== "completed",
      issueCount: issueCountFromCounts(progress.counts),
      navigate,
    };

    const claimed = runId === null || claimRunToast(runId);
    if (progress.status !== "cancelled" && claimed) {
      if (buildToast) {
        buildToast(context);
      } else {
        toasts.runCompleted({
          operationType,
          counts: progress.counts ?? {},
          issueCount: context.issueCount,
          runId,
          failed: context.failed,
          onNavigate: navigate,
        });
      }
    }
    onTerminal?.(context);
  });

  const terminal = isTerminalProgress(progress);
  useEffect(() => {
    if (terminal) announce();
    // `announce` is an Effect Event: it reads the latest props without
    // re-running this Effect, which fires only on the transition to terminal.
  }, [terminal]);
}
