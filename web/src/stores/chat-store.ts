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
  /** Sent by the backend on tool_start; absent on frames from older streams. */
  kind?: ToolKind;
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

/**
 * The workflow the assistant is proposing to create or edit. Populated by a
 * later phase (tool results carry the draft); dormant in Phase 0. `source`
 * distinguishes a brand-new workflow from an edit to an existing one.
 */
export interface WorkflowDraft {
  action_id: string;
  def: unknown;
  source: "new" | { workflow_id: string };
}

interface ChatState {
  // Conversation data
  messages: ChatMessage[];
  isStreaming: boolean;
  abortController: AbortController | null;
  isPanelOpen: boolean;
  confirmationStates: Record<string, ConfirmationState>;
  effort: EffortChoice;
  currentWorkflowDraft: WorkflowDraft | null;

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
    kind?: ToolKind,
  ) => void;
  setToolResult: (
    messageId: string,
    toolId: string,
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
  setPanelOpen: (open: boolean) => void;
  togglePanel: () => void;
  clearMessages: () => void;

  // Actions - confirmation, effort, workflow draft
  setConfirmationState: (actionId: string, state: ConfirmationState) => void;
  setEffort: (choice: EffortChoice) => void;
  setCurrentWorkflowDraft: (draft: WorkflowDraft | null) => void;
}

/** The empty-conversation state, fresh each call (new array/object refs).
 *  Single source for the store's initial values and `clearMessages`, so they
 *  can't drift apart. Excludes `isPanelOpen` and `effort`, which survive a
 *  "New conversation" (the panel stays open; the effort choice is a persisted
 *  preference). */
function initialConversationState(): Pick<
  ChatState,
  | "messages"
  | "isStreaming"
  | "abortController"
  | "confirmationStates"
  | "currentWorkflowDraft"
> {
  return {
    messages: [],
    isStreaming: false,
    abortController: null,
    confirmationStates: {},
    currentWorkflowDraft: null,
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
      if (s.messages.length >= MESSAGE_CAP) return { isStreaming: true };
      added = true;
      return {
        messages: [
          ...s.messages,
          { id, role: "assistant" as const, content: "", isStreaming: true },
        ],
        isStreaming: true,
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
      isStreaming: false,
      abortController: null,
    })),

  setMessageError: (id, code, message) =>
    set((s) => ({
      messages: updateMessage(s.messages, id, (m) => ({
        ...m,
        isStreaming: false,
        error: { code, message },
      })),
      isStreaming: false,
      abortController: null,
    })),

  removeLastAssistantMessage: () =>
    set((s) => {
      const last = s.messages.at(-1);
      if (last?.role !== "assistant") return s;
      return { messages: s.messages.slice(0, -1) };
    }),

  // Tool + code executions
  addToolCall: (messageId, toolId, name, kind = "read") =>
    set((s) => ({
      messages: updateMessage(s.messages, messageId, (m) => ({
        ...m,
        toolCalls: [...(m.toolCalls ?? []), { id: toolId, name, kind }],
      })),
    })),

  setToolResult: (messageId, toolId, result, isError) =>
    set((s) => ({
      messages: updateMessage(s.messages, messageId, (m) => ({
        ...m,
        toolCalls: (m.toolCalls ?? []).map((tc) =>
          tc.id === toolId ? { ...tc, result, isError } : tc,
        ),
      })),
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

  setPanelOpen: (open) => set({ isPanelOpen: open }),

  togglePanel: () => set((s) => ({ isPanelOpen: !s.isPanelOpen })),

  clearMessages: () => set(initialConversationState()),

  // Confirmation, effort, workflow draft
  setConfirmationState: (actionId, state) =>
    set((s) => ({
      confirmationStates: { ...s.confirmationStates, [actionId]: state },
    })),

  setEffort: (choice) => {
    storeEffort(choice);
    set({ effort: choice });
  },

  setCurrentWorkflowDraft: (draft) => set({ currentWorkflowDraft: draft }),
}));

// --- Pure helpers over conversation state -----------------------------------

interface SaveProposalDetails {
  definition?: unknown;
  workflow_id?: unknown;
}

/**
 * Absorb a save_workflow proposal into `currentWorkflowDraft`. Called from the
 * stream callbacks on every tool result; ignores everything that isn't a
 * pending save proposal. The proposal's `details` carry the full normalized
 * definition, so the draft is complete from this single event — refine turns
 * replace it wholesale (keyed by the fresh action_id).
 */
export function absorbWorkflowDraft(
  name: string,
  result: unknown,
  isError: boolean,
): void {
  if (name !== "save_workflow" || isError) return;
  if (!result || typeof result !== "object") return;
  const r = result as {
    status?: unknown;
    action_id?: unknown;
    details?: SaveProposalDetails;
  };
  if (r.status !== "pending_confirmation" || typeof r.action_id !== "string") {
    return;
  }
  const workflowId = r.details?.workflow_id;
  useChatStore.getState().setCurrentWorkflowDraft({
    action_id: r.action_id,
    def: r.details?.definition,
    source:
      typeof workflowId === "string" ? { workflow_id: workflowId } : "new",
  });
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
