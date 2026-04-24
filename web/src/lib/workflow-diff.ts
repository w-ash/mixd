/**
 * Diff computation between two workflow definitions.
 *
 * Compares task-by-task using task IDs as stable keys, detecting
 * added, removed, modified, and unchanged nodes for visual highlighting.
 */

import type { WorkflowTaskDefSchemaInput } from "#/api/generated/model";

export type DiffStatus = "added" | "removed" | "modified" | "unchanged";

export interface WorkflowDiff {
  added: WorkflowTaskDefSchemaInput[];
  removed: WorkflowTaskDefSchemaInput[];
  modified: Array<{
    old: WorkflowTaskDefSchemaInput;
    new: WorkflowTaskDefSchemaInput;
  }>;
  unchanged: WorkflowTaskDefSchemaInput[];
  /** Map from task ID → diff status, for highlight coloring */
  highlightMap: Map<string, DiffStatus>;
}

function tasksEqual(
  a: WorkflowTaskDefSchemaInput,
  b: WorkflowTaskDefSchemaInput,
): boolean {
  if (a.type !== b.type) return false;

  const aUpstream = [...(a.upstream ?? [])].sort();
  const bUpstream = [...(b.upstream ?? [])].sort();
  if (aUpstream.length !== bUpstream.length) return false;
  if (aUpstream.some((v, i) => v !== bUpstream[i])) return false;

  const aConfig = JSON.stringify(a.config ?? {});
  const bConfig = JSON.stringify(b.config ?? {});
  return aConfig === bConfig;
}

export function diffWorkflowDefs(
  oldTasks: WorkflowTaskDefSchemaInput[],
  newTasks: WorkflowTaskDefSchemaInput[],
): WorkflowDiff {
  const oldMap = new Map(oldTasks.map((t) => [t.id, t]));
  const newMap = new Map(newTasks.map((t) => [t.id, t]));

  const added: WorkflowTaskDefSchemaInput[] = [];
  const removed: WorkflowTaskDefSchemaInput[] = [];
  const modified: Array<{
    old: WorkflowTaskDefSchemaInput;
    new: WorkflowTaskDefSchemaInput;
  }> = [];
  const unchanged: WorkflowTaskDefSchemaInput[] = [];
  const highlightMap = new Map<string, DiffStatus>();

  // Check old tasks: removed or modified
  for (const [id, oldTask] of oldMap) {
    const newTask = newMap.get(id);
    if (!newTask) {
      removed.push(oldTask);
      highlightMap.set(id, "removed");
    } else if (!tasksEqual(oldTask, newTask)) {
      modified.push({ old: oldTask, new: newTask });
      highlightMap.set(id, "modified");
    } else {
      unchanged.push(oldTask);
      highlightMap.set(id, "unchanged");
    }
  }

  // Check new tasks: added
  for (const [id, newTask] of newMap) {
    if (!oldMap.has(id)) {
      added.push(newTask);
      highlightMap.set(id, "added");
    }
  }

  return { added, removed, modified, unchanged, highlightMap };
}
