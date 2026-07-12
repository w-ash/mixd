// Kept free of component imports so ToolResultCard's dispatcher can decide
// whether to render the card from the type guard alone — mirrors
// `workflow-preview-types.ts`. (The card itself is light enough to import
// eagerly; this sidecar exists for symmetry and to keep the guard testable.)

/**
 * The tool-result summary a long-running chat tool emits once it has kicked off
 * a background operation. The card subscribes to `operation_id` and streams
 * progress to a terminal state; `run_id` (when present) deep-links to the
 * persistent run-detail row.
 */
export interface OperationStartedResult {
  status: "operation_started";
  operation_id: string;
  run_id: string | null;
  description: string;
}

export function isOperationStartedResult(
  result: unknown,
): result is OperationStartedResult {
  if (!result || typeof result !== "object") return false;
  const r = result as Record<string, unknown>;
  return r.status === "operation_started" && typeof r.operation_id === "string";
}
