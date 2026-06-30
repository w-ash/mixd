import { useEffect } from "react";
import { useLocation } from "react-router";

import type { WorkflowDefSchemaInput } from "#/api/generated/model";
import { resolveEditorEntry } from "#/lib/editor-entry";
import { useEditorStore } from "#/stores/editor-store";

/** The fetched existing workflow the editor hydrates from in the `load` intent. */
export interface LoadedWorkflow {
  id: string;
  definition: WorkflowDefSchemaInput;
}

/**
 * Populate the editor canvas from the entry-intent state machine
 * (see `lib/editor-entry.ts`):
 *
 *   load  → `loadWorkflow(def, id)` once the fetched workflow arrives
 *   seed  → adopt the pre-seeded draft as-is (import / future template-as-draft)
 *   blank → `resetWorkflow()` so a prior edit can't leak onto a fresh canvas
 *
 * The store survives navigation, so the intent is *declared* by the route (a
 * `:id` param → load) or the navigation that seeded it (a typed
 * `EDITOR_SEED_STATE` location-state → seed), never inferred from mount order.
 */
export function useEditorEntry(
  workflowId: string | null,
  workflow: LoadedWorkflow | undefined,
): void {
  const location = useLocation();
  const loadWorkflow = useEditorStore((s) => s.loadWorkflow);

  // `load`: hydrate from the fetched existing workflow when it arrives.
  useEffect(() => {
    if (workflow) loadWorkflow(workflow.definition, workflow.id);
  }, [workflow, loadWorkflow]);

  // `blank` vs `seed` on a fresh `/workflows/new`. Reset only on a genuine blank
  // entry; a seed navigation (import) already handed the store a draft to keep.
  // Resolving the intent explicitly keeps this idempotent — the reset branch is
  // reachable only when the location is itself a no-seed `/workflows/new`, which
  // is exactly when a fresh canvas is wanted, so an in-progress draft is never
  // wiped by a re-render.
  useEffect(() => {
    if (resolveEditorEntry(workflowId, location.state) === "blank") {
      useEditorStore.getState().resetWorkflow();
    }
  }, [workflowId, location.state]);
}
