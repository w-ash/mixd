import { lazy, Suspense } from "react";
import { ConfirmationCard } from "#/components/chat/ConfirmationCard";
import { OperationProgressCard } from "#/components/chat/cards/OperationProgressCard";
import { isOperationStartedResult } from "#/components/chat/cards/operation-progress-types";
import {
  findSaveProposal,
  hasGeneratePreview,
  isGenerateWorkflowResult,
  isPendingConfirmation,
  projectSaveDetails,
} from "#/components/chat/cards/workflow-preview-types";
import type { ToolCall } from "#/stores/chat-store";

// Lazy: the preview card pulls in React Flow + ELK, which must stay out of
// the chat panel's initial bundle (it mounts on every page).
const WorkflowPreviewCard = lazy(() =>
  import("#/components/chat/cards/WorkflowPreviewCard").then((m) => ({
    default: m.WorkflowPreviewCard,
  })),
);

// --- Generic renderer (default for every tool without a bespoke card) ---
//
// Phase 0 ships only the domain-agnostic key/value + list renderer plus the
// confirmation dispatch. Bespoke mixd cards (playlist preview, track table)
// slot into the `switch` in the dispatcher below in a later phase.

function formatGenericValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value === "string") return value;
  if (typeof value === "number") return String(value);
  if (typeof value === "object") {
    return Object.entries(value as Record<string, unknown>)
      .map(([k, v]) => `${formatGenericKey(k)}: ${formatGenericValue(v)}`)
      .join(" · ");
  }
  return String(value);
}

function formatGenericKey(key: string): string {
  return key.replaceAll("_", " ");
}

function GenericListTable({ rows }: { rows: Record<string, unknown>[] }) {
  const columns = Object.keys(rows[0]);
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-border text-left">
            {columns.map((col) => (
              <th
                key={col}
                className="py-1 pr-2 font-display font-medium text-text-muted"
              >
                {formatGenericKey(col)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            // biome-ignore lint/suspicious/noArrayIndexKey: rows are static render-once data
            <tr key={i} className="border-b border-border-muted">
              {columns.map((col) => (
                <td key={col} className="py-1 pr-2 tabular-nums">
                  {formatGenericValue(row[col])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function GenericToolResultCard({
  result,
}: {
  result: Record<string, unknown>;
}) {
  const entries = Object.entries(result);
  if (entries.length === 0) return null;
  const scalars = entries.filter(([, v]) => !Array.isArray(v));
  const lists = entries.filter((entry): entry is [string, unknown[]] =>
    Array.isArray(entry[1]),
  );

  return (
    <div className="space-y-2 text-xs text-text">
      {scalars.length > 0 && (
        <div className="space-y-0.5">
          {scalars.map(([key, value]) => (
            <div key={key} className="flex justify-between gap-4">
              <span className="text-text-muted">{formatGenericKey(key)}</span>
              <span className="text-right tabular-nums">
                {formatGenericValue(value)}
              </span>
            </div>
          ))}
        </div>
      )}
      {lists.map(([key, items]) => (
        <div key={key} className="space-y-0.5">
          <p className="font-display font-medium text-text-muted">
            {formatGenericKey(key)}
          </p>
          {items.length === 0 ? (
            <p className="text-text-muted">none</p>
          ) : typeof items[0] === "object" && items[0] !== null ? (
            <GenericListTable rows={items as Record<string, unknown>[]} />
          ) : (
            <p>{items.map(formatGenericValue).join(", ")}</p>
          )}
        </div>
      ))}
    </div>
  );
}

// --- Main dispatcher ---

export function ToolResultCard({
  toolCall,
  messageId,
  siblingToolCalls,
  onConfirm,
  onCancel,
  onSendMessage,
}: {
  toolCall: ToolCall;
  /** Id of the containing message — lets cards find their triggering prompt. */
  messageId?: string;
  /** All tool calls of the containing message — sibling context for dispatch. */
  siblingToolCalls?: ToolCall[];
  onConfirm?: (actionId: string) => void;
  onCancel?: (actionId: string) => void;
  onSendMessage?: (text: string) => void;
}) {
  if (toolCall.result === undefined || toolCall.isError) return null;
  const result = toolCall.result;

  // This is a hand-written routing chain, not a name→card map — when a second
  // bespoke card lands, convert this to a `Record<toolName, Card>` dispatch.
  //
  // The workflow preview owns the save affordance (Save/Discard on the card),
  // so a sibling save proposal renders nothing of its own.
  if (
    toolCall.name === "generate_workflow_def" &&
    isGenerateWorkflowResult(result)
  ) {
    return (
      <Suspense
        fallback={
          <p className="rounded-lg border border-border-muted px-4 py-2 font-body text-xs text-text-muted">
            Loading preview…
          </p>
        }
      >
        <WorkflowPreviewCard
          toolCallId={toolCall.id}
          messageId={messageId}
          result={result}
          saveProposal={findSaveProposal(siblingToolCalls)}
          onConfirm={onConfirm}
          onCancel={onCancel}
          onSendMessage={onSendMessage}
        />
      </Suspense>
    );
  }

  // Mutation tools return pending_confirmation → render the approval gate.
  if (isPendingConfirmation(result) && onConfirm && onCancel) {
    if (
      toolCall.name === "save_workflow" &&
      hasGeneratePreview(siblingToolCalls)
    ) {
      return null; // the preview card in this message is the approval gate
    }
    const details =
      toolCall.name === "save_workflow"
        ? projectSaveDetails(result.details)
        : result.details;
    return (
      <ConfirmationCard
        actionId={result.action_id}
        description={result.description}
        details={details}
        toolName={toolCall.name}
        onConfirm={onConfirm}
        onCancel={onCancel}
      />
    );
  }

  // A long-running tool's confirmation reports a started background operation —
  // render live SSE progress inline instead of a static key/value dump.
  if (isOperationStartedResult(result)) {
    return <OperationProgressCard result={result} />;
  }

  // Every read tool renders through the generic key/value card until a bespoke
  // mixd card is added here.
  if (result && typeof result === "object" && !Array.isArray(result)) {
    return <GenericToolResultCard result={result as Record<string, unknown>} />;
  }
  return null;
}
