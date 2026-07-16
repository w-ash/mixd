import { MessageSquarePlus, RotateCcw, X } from "lucide-react";
import { useCallback, useState } from "react";
import { matchPath, useLocation } from "react-router";

import {
  type ChatSSECallbacks,
  type ConfirmationPayload,
  sendChatMessage,
} from "#/api/chat-sse";
import { AlertBanner } from "#/components/shared/AlertBanner";
import { Button } from "#/components/ui/button";
import { useKeyboardShortcut } from "#/hooks/useKeyboardShortcut";
import { EFFORT_API_VALUES, EFFORT_OPTIONS } from "#/lib/effort";
import { cn } from "#/lib/utils";
import {
  MESSAGE_CAP,
  SOFT_WARN_THRESHOLD,
  selectIsStreaming,
  useChatStore,
} from "#/stores/chat-store";

import { ChatInput } from "./ChatInput";
import { ChatMessageList } from "./ChatMessageList";
import { SuggestedQuestions } from "./SuggestedQuestions";

const LIMIT_FULL_MESSAGE =
  "This conversation is full. Start a new one to continue.";

const closePanel = () => useChatStore.getState().setPanelOpen(false);

/**
 * Wire the SSE callbacks to the store for a given assistant message. When the
 * turn is resolving a confirmation, the terminal callbacks also commit the
 * card's state: `onDone` records confirmed/cancelled, while `onError` rolls the
 * card back to `pending` so its buttons reappear for a retry.
 */
function buildCallbacks(
  assistantId: string,
  confirmation?: ConfirmationPayload,
): ChatSSECallbacks {
  const store = useChatStore.getState();
  return {
    onToken: (text) => store.appendToken(assistantId, text),
    onToolStart: (name, id, kind) =>
      store.addToolCall(assistantId, id, name, kind),
    onToolResult: (name, id, summary, isError) =>
      store.setToolResult(assistantId, id, name, summary, isError),
    onCodeStart: (id, command) =>
      store.addCodeExecution(assistantId, id, command),
    onCodeResult: (id, stdout, stderr, returnCode) =>
      store.setCodeResult(assistantId, id, stdout, stderr, returnCode),
    onDone: () => {
      store.completeMessage(assistantId);
      if (confirmation) {
        store.setConfirmationState(
          confirmation.action_id,
          confirmation.approved ? "confirmed" : "cancelled",
        );
      }
    },
    onError: (code, message) => {
      store.setMessageError(assistantId, code, message);
      if (confirmation) {
        store.setConfirmationState(confirmation.action_id, "pending");
      }
    },
  };
}

/** Matches the workflow editor route to derive the workflow-in-context id. */
function matchWorkflowEditId(pathname: string): string | undefined {
  return matchPath("/workflows/:id/edit", pathname)?.params.id;
}

// The coarse UI section the user is on, keyed by first path segment (index →
// dashboard). Only sections the server routes tools for are emitted; anything
// else sends no page and degrades to the static core + tool-search. Keep these
// keys in sync with `_PAGE_TOOL_HINTS` in src/application/tools/registry.py.
const SECTION_BY_SEGMENT: Record<string, string> = {
  "": "dashboard",
  playlists: "playlists",
  library: "library",
  workflows: "workflows",
  imports: "imports",
};

function pageSection(pathname: string): string | undefined {
  const segment = pathname.split("/").filter(Boolean)[0] ?? "";
  return SECTION_BY_SEGMENT[segment];
}

/** Open an SSE stream for the freshly-created assistant message. */
function startStream(
  assistantId: string,
  currentWorkflowId?: string,
  confirmation?: ConfirmationPayload,
  page?: string,
) {
  if (!assistantId) return;
  const store = useChatStore.getState();
  // Send the history minus the empty placeholder being generated and any
  // errored turns (they'd confuse the model).
  const apiMessages = store.messages
    .filter((m) => m.id !== assistantId && !m.error)
    .map((m) => ({ role: m.role, content: m.content }));
  const controller = new AbortController();
  store.setAbortController(controller);
  void sendChatMessage(
    apiMessages,
    buildCallbacks(assistantId, confirmation),
    controller.signal,
    confirmation,
    EFFORT_API_VALUES[store.effort],
    currentWorkflowId,
    page,
  );
}

export function ChatPanel({ fullScreen = false }: { fullScreen?: boolean }) {
  const messages = useChatStore((s) => s.messages);
  const isStreaming = useChatStore(selectIsStreaming);
  const effort = useChatStore((s) => s.effort);
  const setEffort = useChatStore((s) => s.setEffort);
  const [limitError, setLimitError] = useState<string | null>(null);
  const { pathname } = useLocation();
  const currentWorkflowId = matchWorkflowEditId(pathname);
  const page = pageSection(pathname);

  const sendQuestion = useCallback(
    (text: string) => {
      const store = useChatStore.getState();
      if (selectIsStreaming(store)) return;

      if (store.messages.length >= MESSAGE_CAP) {
        setLimitError(LIMIT_FULL_MESSAGE);
        return;
      }

      setLimitError(null);
      store.addUserMessage(text);
      startStream(
        store.startAssistantMessage(),
        currentWorkflowId,
        undefined,
        page,
      );
    },
    [currentWorkflowId, page],
  );

  const handleRegenerate = useCallback(() => {
    const store = useChatStore.getState();
    if (selectIsStreaming(store)) return;
    const last = store.messages.at(-1);
    if (last?.role !== "assistant") return;

    store.removeLastAssistantMessage();
    startStream(
      store.startAssistantMessage(),
      currentWorkflowId,
      undefined,
      page,
    );
  }, [currentWorkflowId, page]);

  const handleNewConversation = useCallback(() => {
    const store = useChatStore.getState();
    store.abortController?.abort();
    store.clearMessages();
    setLimitError(null);
  }, []);

  // One resolution path for both Save/Confirm and Discard/Cancel. The card goes
  // to `loading` immediately, then the stream's terminal callback commits it to
  // confirmed/cancelled (onDone) or rolls it back to pending (onError).
  const resolveConfirmation = useCallback(
    (actionId: string, approved: boolean) => {
      const store = useChatStore.getState();
      if (selectIsStreaming(store)) return;
      // Guard the cap BEFORE flipping the card to loading — otherwise
      // startAssistantMessage returns "" and the card sticks in loading.
      if (store.messages.length >= MESSAGE_CAP) {
        setLimitError(LIMIT_FULL_MESSAGE);
        return;
      }
      store.setConfirmationState(actionId, "loading");
      startStream(
        store.startAssistantMessage(),
        currentWorkflowId,
        { action_id: actionId, approved },
        page,
      );
    },
    [currentWorkflowId, page],
  );

  const handleConfirm = useCallback(
    (actionId: string) => resolveConfirmation(actionId, true),
    [resolveConfirmation],
  );

  const handleCancel = useCallback(
    (actionId: string) => resolveConfirmation(actionId, false),
    [resolveConfirmation],
  );

  const handleStop = useCallback(() => {
    const store = useChatStore.getState();
    store.abortController?.abort();
    // The SSE reader returns cleanly on abort (no onDone/onError), so finalize
    // the streaming message here or the UI stays stuck in the thinking state.
    store.stopStreaming();
  }, []);

  // Escape closes the panel (not on the full-screen mobile route).
  useKeyboardShortcut(["Escape"], closePanel, !fullScreen);

  const hasMessages = messages.length > 0;
  const canRegenerate = !isStreaming && messages.at(-1)?.role === "assistant";
  const showNewConversation = hasMessages || limitError !== null;
  const nearLimit =
    !limitError &&
    messages.length >= SOFT_WARN_THRESHOLD &&
    messages.length < MESSAGE_CAP;
  const remaining = MESSAGE_CAP - messages.length;

  return (
    <div className="flex h-full flex-col">
      {!fullScreen && (
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <h2 className="font-display text-sm font-medium text-text">
            Assistant
          </h2>
          <button
            type="button"
            onClick={closePanel}
            className="rounded-md p-1 text-text-muted transition-colors hover:text-text"
            aria-label="Close chat"
          >
            <X className="size-4" />
          </button>
        </div>
      )}

      {hasMessages ? (
        <ChatMessageList
          messages={messages}
          onConfirm={handleConfirm}
          onCancel={handleCancel}
          onSendMessage={sendQuestion}
        />
      ) : (
        <SuggestedQuestions onSelect={sendQuestion} />
      )}

      {(showNewConversation || canRegenerate || nearLimit) && (
        <div className="flex flex-col gap-2 border-t border-border px-4 py-3">
          {limitError && <AlertBanner title={limitError} />}
          {nearLimit && (
            <AlertBanner
              title={`This conversation is getting long — ${remaining} message${
                remaining === 1 ? "" : "s"
              } left before you'll need a new one.`}
            />
          )}
          {(showNewConversation || canRegenerate) && (
            <div className="flex items-center justify-between gap-2">
              {showNewConversation ? (
                <Button
                  size="sm"
                  variant={limitError ? "default" : "secondary"}
                  onClick={handleNewConversation}
                >
                  <MessageSquarePlus className="size-3.5" />
                  New conversation
                </Button>
              ) : (
                <span />
              )}
              {canRegenerate && (
                <Button size="sm" variant="outline" onClick={handleRegenerate}>
                  <RotateCcw className="size-3.5" />
                  Regenerate
                </Button>
              )}
            </div>
          )}
        </div>
      )}

      <fieldset className="flex items-center justify-between gap-2 px-4 pt-2">
        <legend className="sr-only">Reasoning effort</legend>
        <span className="font-display text-xs text-text-muted">Effort</span>
        <div className="inline-flex rounded-full border border-border p-0.5">
          {EFFORT_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              type="button"
              aria-pressed={effort === opt.value}
              onClick={() => setEffort(opt.value)}
              className={cn(
                "rounded-full px-2.5 py-1 font-display text-xs transition-colors",
                effort === opt.value
                  ? "bg-primary text-primary-foreground"
                  : "text-text-muted hover:text-text",
              )}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </fieldset>

      <ChatInput
        onSubmit={sendQuestion}
        isStreaming={isStreaming}
        onStop={handleStop}
      />
    </div>
  );
}
