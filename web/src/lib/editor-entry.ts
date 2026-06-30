/**
 * Workflow-editor entry-intent contract.
 *
 * The editor's Zustand store is a navigation-surviving singleton, so each route
 * into the editor must *declare* how the canvas should be populated rather than
 * relying on mount order (the implicit rule this replaced silently broke as soon
 * as a second seed-style entry point was added). There are three intents:
 *
 *   load  — the route carries a workflow `:id`; the editor fetches it and calls
 *           `loadWorkflow(def, id)`.
 *   seed  — the store was pre-seeded as an unsaved draft *before* navigation
 *           (file import today; template-as-draft later). Adopt it as-is — do
 *           NOT reset.
 *   blank — a fresh `/workflows/new` with no seed; `resetWorkflow()` so a prior
 *           edit or import can't leak onto the canvas.
 *
 * `seed` is signalled by an explicit, typed `location.state` flag set at the
 * navigation that seeded the store (`EDITOR_SEED_STATE`). `load` vs `blank` is
 * decided by the presence of the `:id` route param. Keeping the flag's shape and
 * the resolution in one module means the setter (navigation) and the reader (the
 * editor mount) can't drift apart.
 */

export type EditorEntryIntent = "load" | "seed" | "blank";

/** Typed `location.state` shape a seed-navigation passes. */
export interface EditorSeedLocationState {
  editorEntry: "seed";
}

/** The `location.state` value a seed-navigation must pass (e.g. file import). */
export const EDITOR_SEED_STATE: EditorSeedLocationState = {
  editorEntry: "seed",
};

/** Narrow an unknown `location.state` to whether it declares a seed entry. */
export function isEditorSeedState(
  state: unknown,
): state is EditorSeedLocationState {
  return (
    typeof state === "object" &&
    state !== null &&
    (state as { editorEntry?: unknown }).editorEntry === "seed"
  );
}

/**
 * Resolve the entry intent from the route param and navigation state.
 *
 * A non-null `workflowId` is always `load`. Otherwise a typed seed flag means
 * `seed`; anything else is a genuine `blank` entry.
 */
export function resolveEditorEntry(
  workflowId: string | null,
  locationState: unknown,
): EditorEntryIntent {
  if (workflowId !== null) return "load";
  if (isEditorSeedState(locationState)) return "seed";
  return "blank";
}
