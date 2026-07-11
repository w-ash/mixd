import {
  Check,
  ExternalLink,
  Loader2,
  ThumbsDown,
  ThumbsUp,
  X,
} from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";

import { usePostChatFeedbackApiV1ChatFeedbackPost } from "#/api/generated/chat/chat";
import type { ChatFeedbackRequest } from "#/api/generated/model";
import { WorkflowGraph } from "#/components/shared/WorkflowGraph";
import { Button } from "#/components/ui/button";
import { cn } from "#/lib/utils";
import {
  findLatestGenerateToolCallId,
  findTriggeringPrompt,
  useChatStore,
} from "#/stores/chat-store";

import type {
  GenerateWorkflowResult,
  SaveProposal,
} from "./workflow-preview-types";

// --- Feedback row -------------------------------------------------------------

/**
 * Thumbs on a generated draft. Explicit thumbs are the only recorded
 * feedback — a save is its own durable acceptance signal. Thumbs-down opens
 * a note field first (the most actionable feedback per the v0.9.0 spec);
 * thumbs-up posts immediately.
 */
function FeedbackRow({
  workflowDef,
  messageId,
}: {
  workflowDef: GenerateWorkflowResult["workflow_def"];
  messageId?: string;
}) {
  const [submitted, setSubmitted] = useState(false);
  const [noteOpen, setNoteOpen] = useState(false);
  const [note, setNote] = useState("");
  const feedback = usePostChatFeedbackApiV1ChatFeedbackPost();

  const submit = (signal: "positive" | "negative", noteText?: string) => {
    const prompt =
      (messageId &&
        findTriggeringPrompt(useChatStore.getState().messages, messageId)) ||
      "";
    feedback.mutate({
      data: {
        prompt,
        // Structurally JSON either way; the generated JsonValueInput union
        // doesn't unify with the typed task shape.
        generated_workflow_def:
          workflowDef as unknown as ChatFeedbackRequest["generated_workflow_def"],
        signal,
        note: noteText || null,
      },
    });
    setSubmitted(true);
    setNoteOpen(false);
  };

  if (submitted) {
    return (
      <p className="mt-2 font-body text-xs text-text-muted">
        Thanks for the feedback.
      </p>
    );
  }

  return (
    <div className="mt-2 border-t border-border-muted pt-2">
      <div className="flex items-center justify-between gap-2">
        <span className="font-body text-xs text-text-muted">
          Was this draft helpful?
        </span>
        <div className="flex items-center gap-1">
          <button
            type="button"
            aria-label="Good draft"
            onClick={() => submit("positive")}
            className="rounded-md p-1.5 text-text-muted transition-colors hover:text-status-success"
          >
            <ThumbsUp className="size-3.5" />
          </button>
          <button
            type="button"
            aria-label="Poor draft"
            aria-expanded={noteOpen}
            onClick={() => setNoteOpen((open) => !open)}
            className={cn(
              "rounded-md p-1.5 text-text-muted transition-colors hover:text-destructive",
              noteOpen && "text-destructive",
            )}
          >
            <ThumbsDown className="size-3.5" />
          </button>
        </div>
      </div>
      {noteOpen && (
        <div className="mt-2 flex flex-col gap-2">
          <textarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="What was off about it? (optional)"
            aria-label="Feedback note"
            rows={2}
            maxLength={2000}
            className="w-full rounded-md border border-border bg-surface-sunken px-2 py-1.5 font-body text-xs text-text placeholder:text-text-muted"
          />
          <Button
            size="sm"
            variant="secondary"
            className="self-end"
            onClick={() => submit("negative", note.trim())}
          >
            Send feedback
          </Button>
        </div>
      )}
    </div>
  );
}

// --- Card ---------------------------------------------------------------------

/**
 * Renders a generate_workflow_def result as the same read-only DAG the rest of
 * the app shows, with the save affordance attached. Save confirms the sibling
 * save_workflow proposal (the model proposes it in the same turn per the
 * primer's mutation rules); when no live proposal exists — model skipped it or
 * the pending action expired — Save degrades to a synthetic chat message,
 * which re-proposes. Superseded drafts (an older generate call once a newer
 * one exists) collapse to a one-line note so refinement never leaves stale
 * full-size graphs to scroll past.
 */
export function WorkflowPreviewCard({
  toolCallId,
  messageId,
  result,
  saveProposal,
  onConfirm,
  onCancel,
  onSendMessage,
}: {
  toolCallId: string;
  messageId?: string;
  result: GenerateWorkflowResult;
  saveProposal?: SaveProposal;
  onConfirm?: (actionId: string) => void;
  onCancel?: (actionId: string) => void;
  onSendMessage?: (text: string) => void;
}) {
  const latestGenerateId = useChatStore((s) =>
    findLatestGenerateToolCallId(s.messages),
  );
  const draft = useChatStore((s) => s.currentWorkflowDraft);
  const isStreaming = useChatStore((s) => s.isStreaming);

  // Fallback for cross-turn proposals: the store draft is the live proposal
  // when the save landed in a different message than this generate call.
  const proposal =
    saveProposal ??
    (draft
      ? {
          actionId: draft.action_id,
          mode:
            draft.source === "new" ? ("create" as const) : ("update" as const),
        }
      : undefined);
  const state = useChatStore((s) =>
    proposal
      ? (s.confirmationStates[proposal.actionId] ?? "pending")
      : undefined,
  );

  const superseded =
    latestGenerateId !== null && latestGenerateId !== toolCallId;
  if (superseded) {
    return (
      <p className="rounded-lg border border-border-muted px-4 py-2 font-body text-xs italic text-text-muted">
        Replaced by a newer draft below.
      </p>
    );
  }

  const def = result.workflow_def;
  const resolved = state === "confirmed" || state === "cancelled";
  const saveLabel =
    proposal?.mode === "update" ? "Save changes" : "Save workflow";

  const handleSave = () => {
    if (proposal && onConfirm) {
      onConfirm(proposal.actionId);
    } else {
      onSendMessage?.("Save this workflow.");
    }
  };
  const handleDiscard = () => {
    if (proposal && onCancel) {
      onCancel(proposal.actionId);
    } else {
      onSendMessage?.("Discard this draft — don't save it.");
    }
  };

  return (
    <div className="w-full rounded-lg border-l-2 border-primary bg-surface px-4 py-3">
      <div className="mb-1 flex items-baseline justify-between gap-2">
        <p className="font-display text-xs font-medium text-text">{def.name}</p>
        <span className="shrink-0 font-mono text-[10px] text-text-muted">
          {result.task_count} node{result.task_count === 1 ? "" : "s"}
        </span>
      </div>
      {def.description && (
        <p className="mb-2 font-body text-xs text-text-muted">
          {def.description}
        </p>
      )}

      <div className="h-64 w-full overflow-hidden rounded-md border border-border-muted">
        <WorkflowGraph tasks={def.tasks} />
      </div>

      {result.warnings.length > 0 && (
        <ul className="mt-2 space-y-0.5">
          {result.warnings.map((w) => (
            <li
              key={`${w.task_id}-${w.field}-${w.message}`}
              className="font-body text-xs text-status-warning"
            >
              {w.task_id ? `${w.task_id}: ` : ""}
              {w.message}
            </li>
          ))}
        </ul>
      )}

      <div className="mt-3 flex items-center gap-2">
        {resolved ? (
          <span
            className={cn(
              "inline-flex items-center gap-1 font-display text-xs font-medium",
              state === "confirmed" ? "text-status-success" : "text-text-muted",
            )}
          >
            {state === "confirmed" ? (
              <>
                <Check className="size-3.5" />
                Saved
              </>
            ) : (
              <>
                <X className="size-3.5" />
                Discarded
              </>
            )}
          </span>
        ) : (
          <>
            <Button
              variant="default"
              size="sm"
              disabled={isStreaming || state === "loading"}
              onClick={handleSave}
            >
              {state === "loading" && (
                <Loader2 className="size-3.5 animate-spin" />
              )}
              {saveLabel}
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={isStreaming || state === "loading"}
              onClick={handleDiscard}
            >
              Discard
            </Button>
          </>
        )}
        {state === "confirmed" && (
          <Link
            to="/workflows"
            className="ml-auto inline-flex items-center gap-1 font-display text-xs text-primary underline-offset-2 hover:underline"
          >
            <ExternalLink className="size-3" />
            Open in workflows
          </Link>
        )}
      </div>

      <FeedbackRow workflowDef={def} messageId={messageId} />
    </div>
  );
}
