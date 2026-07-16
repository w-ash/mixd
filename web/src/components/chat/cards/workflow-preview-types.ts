import type { WorkflowTaskDefSchemaInput } from "#/api/generated/model";
import type { ToolCall } from "#/stores/chat-store";

// Kept free of component imports: ToolResultCard lazy-loads the preview card
// so React Flow + ELK stay out of the chat panel's initial bundle (and out of
// every test that renders chat without a workflow preview) — the dispatcher
// needs only these types and the guard to decide whether to load it. The
// save_workflow-specific knowledge (proposal detection, definition-stripping)
// lives here too, so the dispatcher only routes.

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

// --- save_workflow proposal helpers (used by the ToolResultCard dispatcher) ---

interface PendingConfirmationResult {
  status: "pending_confirmation";
  action_id: string;
  description: string;
  details: Record<string, unknown>;
}

export function isPendingConfirmation(
  result: unknown,
): result is PendingConfirmationResult {
  if (!result || typeof result !== "object") return false;
  return (result as Record<string, unknown>).status === "pending_confirmation";
}

/** The live save_workflow proposal among a message's tool calls, if any. */
export function findSaveProposal(
  siblings: ToolCall[] | undefined,
): SaveProposal | undefined {
  for (const sibling of siblings ?? []) {
    if (sibling.name !== "save_workflow" || sibling.isError) continue;
    if (!isPendingConfirmation(sibling.result)) continue;
    return {
      actionId: sibling.result.action_id,
      mode: sibling.result.details.mode === "update" ? "update" : "create",
    };
  }
  return undefined;
}

export function hasGeneratePreview(siblings: ToolCall[] | undefined): boolean {
  return (siblings ?? []).some(
    (tc) =>
      tc.name === "generate_workflow_def" &&
      !tc.isError &&
      isGenerateWorkflowResult(tc.result),
  );
}

/**
 * The confirmation `details` minus the embedded `definition` — a JSON wall the
 * graph already renders, so the confirmation summary keeps only human-readable
 * fields.
 */
export function projectSaveDetails(
  details: Record<string, unknown>,
): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(details).filter(([key]) => key !== "definition"),
  );
}
