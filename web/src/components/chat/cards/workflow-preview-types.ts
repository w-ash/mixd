import type { WorkflowTaskDefSchemaInput } from "#/api/generated/model";

// Kept free of component imports: ToolResultCard lazy-loads the preview card
// so React Flow + ELK stay out of the chat panel's initial bundle (and out of
// every test that renders chat without a workflow preview) — the dispatcher
// needs only these types and the guard to decide whether to load it.

export interface ValidationFinding {
  task_id: string;
  field: string;
  message: string;
}

export interface GenerateWorkflowResult {
  status: "valid";
  workflow_def: {
    id: string;
    name: string;
    description: string;
    version: string;
    tasks: WorkflowTaskDefSchemaInput[];
  };
  warnings: ValidationFinding[];
  task_count: number;
}

export function isGenerateWorkflowResult(
  result: unknown,
): result is GenerateWorkflowResult {
  if (!result || typeof result !== "object") return false;
  const r = result as Record<string, unknown>;
  if (r.status !== "valid") return false;
  const def = r.workflow_def as Record<string, unknown> | undefined;
  return (
    !!def &&
    typeof def.name === "string" &&
    Array.isArray(def.tasks) &&
    def.tasks.every(
      (t: unknown) =>
        !!t &&
        typeof t === "object" &&
        typeof (t as Record<string, unknown>).id === "string" &&
        typeof (t as Record<string, unknown>).type === "string",
    )
  );
}

/** The save proposal a preview card's Save button confirms. */
export interface SaveProposal {
  actionId: string;
  mode: "create" | "update";
}
