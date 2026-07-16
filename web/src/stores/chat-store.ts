import { create } from "zustand";

import { type EffortChoice, getStoredEffort, storeEffort } from "#/lib/effort";

/**
 * Hard cap on messages retained in a single conversation. Both message-adding
 * actions refuse to grow the array past this, so the UI guard and the store
 * agree even if a caller forgets to check. Start a new conversation to reset.
 */
export const MESSAGE_CAP = 50;

/**
 * Soft threshold: once the conversation reaches this many messages the UI
 * shows a "running low on room" nudge, while sending still works up to the cap.
 */
export const SOFT_WARN_THRESHOLD = 45;

export type ToolKind = "read" | "write" | "agentic";

export interface ToolCall {
  id: string;
  name: string;
  /** Side-effect class. Coerced once at the untyped-JSON SSE boundary. */
  kind: ToolKind;
  result?: unknown;
  isError?: boolean;
}

export interface CodeExecution {
  id: string;
  command: string;
  stdout?: string;
  stderr?: string;
  returnCode?: number;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  isStreaming?: boolean;
  error?: { code: string; message: string };
  toolCalls?: ToolCall[];
  codeExecutions?: CodeExecution[];
}

export type ConfirmationState =
  | "pending"
  | "loading"
  | "confirmed"
  | "cancelled";

interface ChatState {
  // Conversation data
  messages: ChatMessage[];
  abortController: AbortController | null;
  isPanelOpen: boolean;
  confirmationStates: Record<string, ConfirmationState>;
  effort: EffortChoice;

  // Actions - message lifecycle
  addUserMessage: (text: string) => string;
  startAssistantMessage: () => string;
  appendToken: (id: string, text: string) => void;
  completeMessage: (id: string) => void;
  setMessageError: (id: string, code: string, message: string) => void;
  removeLastAssistantMessage: () => void;

  // Actions - tool + code executions
  addToolCall: (
    messageId: string,
    toolId: string,
    name: string,
    kind: ToolKind,
  ) => void;
  setToolResult: (
    messageId: string,
    toolId: string,
    name: string,
    result: unknown,
    isError: boolean,
  ) => void;
  addCodeExecution: (
    messageId: string,
    codeId: string,
    command: string,
  ) => void;
  setCodeResult: (
    messageId: string,
    codeId: string,
    stdout: string,
    stderr: string,
    returnCode: number,
  ) => void;

  // Actions - panel + streaming
  setAbortController: (c: AbortController | null) => void;
  stopStreaming: () => void;
  setPanelOpen: (open: boolean) => void;
  togglePanel: () => void;
  clearMessages: () => void;

  // Actions - confirmation, effort
  setConfirmationState: (actionId: string, state: ConfirmationState) => void;
  setEffort: (choice: EffortChoice) => void;
}

/**
 * Whether any message in the conversation is still streaming. Derived from the
 * messages rather than a stored flag, so display and per-message state can
 * never disagree (there is no global `isStreaming` to fall out of sync).
 */
export const selectIsStreaming = (s: ChatState): boolean =>
  s.messages.some((m) => m.isStreaming);

/** The empty-conversation state, fresh each call (new array/object refs).
 *  Single source for the store's initial values and `clearMessages`, so they
 *  can't drift apart. Excludes `isPanelOpen` and `effort`, which survive a
 *  "New conversation" (the panel stays open; the effort choice is a persisted
 *  preference). */
function initialConversationState(): Pick<
  ChatState,
  "messages" | "abortController" | "confirmationStates"
> {
  return {
    messages: [],
    abortController: null,
    confirmationStates: {},
  };
}

function updateMessage(
  messages: ChatMessage[],
  id: string,
  updater: (msg: ChatMessage) => ChatMessage,
): ChatMessage[] {
  return messages.map((m) => (m.id === id ? updater(m) : m));
}

export const useChatStore = create<ChatState>()((set) => ({
  // Initial state
  ...initialConversationState(),
  isPanelOpen: false,
  effort: getStoredEffort(),

  // Message lifecycle
  addUserMessage: (text) => {
    const id = crypto.randomUUID();
    let added = false;
    set((s) => {
      // Defense in depth: the panel guards the send, but the store is the last
      // word on the cap so no caller can grow the array past it.
      if (s.messages.length >= MESSAGE_CAP) return s;
      added = true;
      return {
        messages: [...s.messages, { id, role: "user" as const, content: text }],
      };
    });
    return added ? id : "";
  },

  startAssistantMessage: () => {
    const id = crypto.randomUUID();
    let added = false;
    set((s) => {
      if (s.messages.length >= MESSAGE_CAP) return s;
      added = true;
      return {
        messages: [
          ...s.messages,
          { id, role: "assistant" as const, content: "", isStreaming: true },
        ],
      };
    });
    return added ? id : "";
  },

  appendToken: (id, text) =>
    set((s) => ({
      messages: updateMessage(s.messages, id, (m) => ({
        ...m,
        content: m.content + text,
      })),
    })),

  completeMessage: (id) =>
    set((s) => ({
      messages: updateMessage(s.messages, id, (m) => ({
        ...m,
        isStreaming: false,
      })),
      abortController: null,
    })),

  setMessageError: (id, code, message) =>
    set((s) => ({
      messages: updateMessage(s.messages, id, (m) => ({
        ...m,
        isStreaming: false,
        error: { code, message },
      })),
      abortController: null,
    })),

  removeLastAssistantMessage: () =>
    set((s) => {
      const last = s.messages.at(-1);
      if (last?.role !== "assistant") return s;
      return { messages: s.messages.slice(0, -1) };
    }),

  // Tool + code executions
  addToolCall: (messageId, toolId, name, kind) =>
    set((s) => ({
      messages: updateMessage(s.messages, messageId, (m) => ({
        ...m,
        toolCalls: [...(m.toolCalls ?? []), { id: toolId, name, kind }],
      })),
    })),

  setToolResult: (messageId, toolId, name, result, isError) =>
    set((s) => ({
      messages: updateMessage(s.messages, messageId, (m) => {
        const calls = m.toolCalls ?? [];
        // UPSERT: a synthetic operation_started frame arrives as a tool_result
        // with no preceding tool_start, so append a fresh ToolCall for it (a
        // write) instead of dropping the frame — that lets the card render.
        const exists = calls.some((tc) => tc.id === toolId);
        return {
          ...m,
          toolCalls: exists
            ? calls.map((tc) =>
                tc.id === toolId ? { ...tc, result, isError } : tc,
              )
            : [...calls, { id: toolId, name, kind: "write", result, isError }],
        };
      }),
    })),

  addCodeExecution: (messageId, codeId, command) =>
    set((s) => ({
      messages: updateMessage(s.messages, messageId, (m) => ({
        ...m,
        codeExecutions: [...(m.codeExecutions ?? []), { id: codeId, command }],
      })),
    })),

  setCodeResult: (messageId, codeId, stdout, stderr, returnCode) =>
    set((s) => ({
      messages: updateMessage(s.messages, messageId, (m) => ({
        ...m,
        codeExecutions: (m.codeExecutions ?? []).map((ce) =>
          ce.id === codeId ? { ...ce, stdout, stderr, returnCode } : ce,
        ),
      })),
    })),

  // Panel + streaming
  setAbortController: (c) => set({ abortController: c }),

  // Finalize a user-aborted stream: clear the trailing streaming flag(s) so the
  // UI leaves the "thinking" state, and drop the controller. The SSE reader
  // returns cleanly on abort, so no onDone/onError fires to do this for us.
  stopStreaming: () =>
    set((s) => ({
      messages: s.messages.map((m) =>
        m.isStreaming ? { ...m, isStreaming: false } : m,
      ),
      abortController: null,
    })),

  setPanelOpen: (open) => set({ isPanelOpen: open }),

  togglePanel: () => set((s) => ({ isPanelOpen: !s.isPanelOpen })),

  clearMessages: () => set(initialConversationState()),

  // Confirmation, effort
  setConfirmationState: (actionId, state) =>
    set((s) => ({
      confirmationStates: { ...s.confirmationStates, [actionId]: state },
    })),

  setEffort: (choice) => {
    storeEffort(choice);
    set({ effort: choice });
  },
}));

// --- Pure helpers over conversation state -----------------------------------

/**
 * The newest live save_workflow proposal in the conversation — scanning from
 * the end for the last non-error `save_workflow` result still in
 * `pending_confirmation`. A preview card whose own turn carried no save
 * proposal (the model saved in a later turn, or the pending action expired)
 * uses this cross-turn fallback to find the action_id its Save button confirms.
 * Mirrors `findSaveProposal`'s `details.mode` convention. Returns the same
 * `{ actionId, mode }` shape as `SaveProposal` in `workflow-preview-types`.
 */
export function findLatestSaveProposal(
  messages: ChatMessage[],
): { actionId: string; mode: "create" | "update" } | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    const calls = messages[i].toolCalls ?? [];
    for (let j = calls.length - 1; j >= 0; j--) {
      const tc = calls[j];
      if (tc.name !== "save_workflow" || tc.isError) continue;
      const r = tc.result as
        | {
            status?: unknown;
            action_id?: unknown;
            details?: { mode?: unknown };
          }
        | undefined;
      if (
        r?.status !== "pending_confirmation" ||
        typeof r.action_id !== "string"
      ) {
        continue;
      }
      return {
        actionId: r.action_id,
        mode: r.details?.mode === "update" ? "update" : "create",
      };
    }
  }
  return null;
}

/**
 * The id of the newest generate_workflow_def tool call in the conversation —
 * older preview cards compare against it and collapse as superseded.
 */
export function findLatestGenerateToolCallId(
  messages: ChatMessage[],
): string | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    const calls = messages[i].toolCalls ?? [];
    for (let j = calls.length - 1; j >= 0; j--) {
      if (calls[j].name === "generate_workflow_def" && !calls[j].isError) {
        return calls[j].id;
      }
    }
  }
  return null;
}

/**
 * The user message that triggered the assistant message containing the given
 * id — the `prompt` recorded with feedback on a generated workflow.
 */
export function findTriggeringPrompt(
  messages: ChatMessage[],
  messageId: string,
): string | null {
  const index = messages.findIndex((m) => m.id === messageId);
  for (let i = (index === -1 ? messages.length : index) - 1; i >= 0; i--) {
    if (messages[i].role === "user") return messages[i].content;
  }
  return null;
}
