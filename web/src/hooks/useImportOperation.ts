/**
 * Local handle on a long-running import a card can start.
 *
 * Every import card differs only in which mutation it fires and what to call
 * that work in a failure toast. The rest is identical: hold the two ids the
 * start response carries, and report the same two failure modes — an envelope
 * the server did not accept, and a transport error. Keeping it here leaves each
 * card a props-only wrapper over its `OperationCard`.
 */

import { useState } from "react";

import type { OperationStartedResponse } from "#/api/generated/model";
import { toasts } from "#/lib/toasts";

/** The `{data, status}` envelope every generated call resolves to. */
interface StartEnvelope {
  status: number;
  data: unknown;
}

/** The slice of a Tanstack mutation this hook drives. */
export interface StartOperationMutation<TVariables> {
  isPending: boolean;
  mutate: (
    variables: TVariables,
    options: {
      onSuccess: (response: StartEnvelope) => void;
      onError: (error: unknown) => void;
    },
  ) => void;
}

export interface ImportOperation<TVariables> {
  /** SSE handle for the started run, or null before the first accepted start. */
  operationId: string | null;
  /** Audit-row id, when the backend persisted one. */
  runId: string | null;
  isPending: boolean;
  /** Fire the mutation. `onStarted` runs only for an accepted start. */
  trigger: (variables: TVariables, onStarted?: () => void) => void;
}

/** Statuses that mean "the run is now the server's problem". */
const ACCEPTED: ReadonlySet<number> = new Set([200, 202]);

export function useImportOperation<TVariables>(
  mutation: StartOperationMutation<TVariables>,
  label: string,
): ImportOperation<TVariables> {
  const [operationId, setOperationId] = useState<string | null>(null);
  const [runId, setRunId] = useState<string | null>(null);

  const trigger = (variables: TVariables, onStarted?: () => void) => {
    mutation.mutate(variables, {
      onSuccess: (response) => {
        if (!ACCEPTED.has(response.status)) {
          toasts.message(`Failed to start ${label}`, {
            description: `Unexpected response (${response.status})`,
          });
          return;
        }
        // Every accepted start shares this body; the envelope is widened to
        // `unknown` so one hook can serve mutations with different error shapes.
        const started = response.data as OperationStartedResponse;
        setOperationId(started.operation_id);
        setRunId(started.run_id ?? null);
        onStarted?.();
      },
      onError: (error) => {
        toasts.error(`Failed to start ${label}`, error);
      },
    });
  };

  return { operationId, runId, trigger, isPending: mutation.isPending };
}
