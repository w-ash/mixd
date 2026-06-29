/**
 * Client-side workflow file export/import.
 *
 * Export serializes the editor's current WorkflowDef to a downloadable `.json`.
 * Import parses + shape-guards a file into a clean WorkflowDef draft, which the
 * editor loads as an unsaved workflow; the server's create path runs the real
 * `validate_workflow_def` on Save (no separate validation endpoint, no backend
 * change). The downloaded file is the portable artifact — back up, move between
 * instances, or share it directly.
 */

import type { WorkflowDefSchemaInput } from "#/api/generated/model";
import { toWorkflowId } from "#/lib/filters-to-workflow";
import { useEditorStore } from "#/stores/editor-store";

/** Trigger a browser download of `def` as `<slug>.json`. */
export function downloadWorkflowDef(def: WorkflowDefSchemaInput): void {
  const json = JSON.stringify(def, null, 2);
  const blob = new Blob([json], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `${toWorkflowId(def.name)}.json`;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

/**
 * Parse + shape-guard raw file text into a clean WorkflowDef draft.
 *
 * Projects to the editable fields only (`{name, description, version, tasks}`),
 * dropping `id` and any server-minted fields (`created_at`/`updated_at`/
 * `user_id`/`version_number`) so a file from a raw row-export imports cleanly
 * and can never smuggle a foreign slug or stale identity into a freshly-created
 * workflow — the server is the sole authority on identity. The returned `id` is
 * a throwaway placeholder: the editor nulls `workflowId` on load and re-stamps
 * the id on Save.
 *
 * @throws Error with a user-facing message when the text isn't valid JSON or
 *   isn't a recognizable Mixd workflow.
 */
export function parseWorkflowFile(text: string): WorkflowDefSchemaInput {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    throw new Error("That file isn't valid JSON.");
  }

  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) {
    throw new Error("That file isn't a Mixd workflow.");
  }

  const obj = raw as Record<string, unknown>;
  if (typeof obj.name !== "string" || obj.name.trim() === "") {
    throw new Error("That file isn't a Mixd workflow — it has no name.");
  }
  if (obj.tasks !== undefined && !Array.isArray(obj.tasks)) {
    throw new Error(
      "That file isn't a Mixd workflow — its tasks aren't a list.",
    );
  }

  // Validate every task carries the fields the editor reads before it loads
  // them — a string id + type (loadWorkflow/buildEdges deref both) and, if
  // present, an array upstream. Without this, a malformed-but-array file (e.g. a
  // task missing its id) seeds a broken canvas while the import reports success.
  const tasks = (obj.tasks ?? []) as unknown[];
  for (const task of tasks) {
    const t = task as Record<string, unknown> | null;
    if (
      typeof t !== "object" ||
      t === null ||
      typeof t.id !== "string" ||
      typeof t.type !== "string" ||
      (t.upstream !== undefined && !Array.isArray(t.upstream))
    ) {
      throw new Error(
        "That file isn't a Mixd workflow — every task needs a string id and type.",
      );
    }
  }

  return {
    // Discarded on Save (editor re-stamps); present only to satisfy the type.
    id: toWorkflowId(obj.name),
    name: obj.name,
    description:
      typeof obj.description === "string" ? obj.description : undefined,
    version: typeof obj.version === "string" ? obj.version : undefined,
    tasks: tasks as WorkflowDefSchemaInput["tasks"],
  };
}

/**
 * Shared import action for both the editor toolbar and the list page: parse the
 * file text into a draft and load it into the editor store as an unsaved
 * (dirty) workflow with no `workflowId`. Save then creates it. Keeping both
 * entry points behind one helper avoids duplicating the parse-and-seed logic.
 *
 * @throws Error (from {@link parseWorkflowFile}) on invalid input — callers
 *   surface it via a toast.
 */
export function loadImportedWorkflowDef(text: string): void {
  const def = parseWorkflowFile(text);
  useEditorStore.getState().loadWorkflow(def);
  useEditorStore.setState({ isDirty: true });
}
