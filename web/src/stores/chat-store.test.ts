import { beforeEach, describe, expect, it } from "vitest";

import {
  absorbWorkflowDraft,
  findLatestGenerateToolCallId,
  findTriggeringPrompt,
  MESSAGE_CAP,
  SOFT_WARN_THRESHOLD,
  useChatStore,
} from "./chat-store";

function reset() {
  useChatStore.setState({
    messages: [],
    isStreaming: false,
    abortController: null,
    isPanelOpen: false,
    confirmationStates: {},
    currentWorkflowDraft: null,
  });
}

describe("chat-store", () => {
  beforeEach(reset);

  it("starts empty with the panel closed and no workflow draft", () => {
    const s = useChatStore.getState();
    expect(s.messages).toEqual([]);
    expect(s.isPanelOpen).toBe(false);
    expect(s.currentWorkflowDraft).toBeNull();
  });

  it("keeps the cap above the soft warning threshold", () => {
    expect(MESSAGE_CAP).toBe(50);
    expect(SOFT_WARN_THRESHOLD).toBe(45);
    expect(SOFT_WARN_THRESHOLD).toBeLessThan(MESSAGE_CAP);
  });

  describe("message lifecycle", () => {
    it("adds a user message and returns its id", () => {
      const id = useChatStore.getState().addUserMessage("hello");
      const { messages } = useChatStore.getState();
      expect(id).not.toBe("");
      expect(messages).toHaveLength(1);
      expect(messages[0]).toMatchObject({
        id,
        role: "user",
        content: "hello",
      });
    });

    it("starts a streaming assistant message", () => {
      const id = useChatStore.getState().startAssistantMessage();
      const { messages, isStreaming } = useChatStore.getState();
      expect(isStreaming).toBe(true);
      expect(messages[0]).toMatchObject({
        id,
        role: "assistant",
        content: "",
        isStreaming: true,
      });
    });

    it("appends tokens then completes the message", () => {
      const id = useChatStore.getState().startAssistantMessage();
      useChatStore.getState().appendToken(id, "Hel");
      useChatStore.getState().appendToken(id, "lo");
      useChatStore.getState().completeMessage(id);
      const { messages, isStreaming } = useChatStore.getState();
      expect(messages[0].content).toBe("Hello");
      expect(messages[0].isStreaming).toBe(false);
      expect(isStreaming).toBe(false);
    });

    it("records a message error and clears streaming", () => {
      const id = useChatStore.getState().startAssistantMessage();
      useChatStore.getState().setMessageError(id, "E_BOOM", "It broke");
      const { messages, isStreaming } = useChatStore.getState();
      expect(messages[0].error).toEqual({
        code: "E_BOOM",
        message: "It broke",
      });
      expect(isStreaming).toBe(false);
    });

    it("removes only a trailing assistant message", () => {
      useChatStore.getState().addUserMessage("q");
      useChatStore.getState().startAssistantMessage();
      useChatStore.getState().removeLastAssistantMessage();
      expect(useChatStore.getState().messages).toHaveLength(1);

      // No-op when the last message is from the user.
      useChatStore.getState().removeLastAssistantMessage();
      expect(useChatStore.getState().messages).toHaveLength(1);
    });
  });

  describe("50-message cap", () => {
    it("refuses user messages past the cap", () => {
      for (let i = 0; i < MESSAGE_CAP + 20; i++) {
        useChatStore.getState().addUserMessage(`m${i}`);
      }
      expect(useChatStore.getState().messages).toHaveLength(MESSAGE_CAP);
    });

    it("returns an empty id when a user message is rejected at the cap", () => {
      for (let i = 0; i < MESSAGE_CAP; i++) {
        useChatStore.getState().addUserMessage(`m${i}`);
      }
      expect(useChatStore.getState().addUserMessage("overflow")).toBe("");
      expect(useChatStore.getState().messages).toHaveLength(MESSAGE_CAP);
    });

    it("refuses assistant messages past the cap", () => {
      for (let i = 0; i < MESSAGE_CAP; i++) {
        useChatStore.getState().addUserMessage(`m${i}`);
      }
      const id = useChatStore.getState().startAssistantMessage();
      expect(id).toBe("");
      expect(useChatStore.getState().messages).toHaveLength(MESSAGE_CAP);
    });
  });

  describe("tool calls", () => {
    it("adds a tool call defaulting to the read kind and records its result", () => {
      const id = useChatStore.getState().startAssistantMessage();
      useChatStore.getState().addToolCall(id, "tc-1", "search_tracks");
      expect(useChatStore.getState().messages[0].toolCalls?.[0]).toMatchObject({
        id: "tc-1",
        name: "search_tracks",
        kind: "read",
      });

      useChatStore.getState().setToolResult(id, "tc-1", { count: 3 }, false);
      expect(useChatStore.getState().messages[0].toolCalls?.[0]).toMatchObject({
        result: { count: 3 },
        isError: false,
      });
    });
  });

  describe("panel + conversation", () => {
    it("toggles and sets the panel open state", () => {
      useChatStore.getState().togglePanel();
      expect(useChatStore.getState().isPanelOpen).toBe(true);
      useChatStore.getState().setPanelOpen(false);
      expect(useChatStore.getState().isPanelOpen).toBe(false);
    });

    it("clearMessages resets the conversation but keeps the panel open", () => {
      useChatStore.getState().setPanelOpen(true);
      useChatStore.getState().addUserMessage("hi");
      useChatStore.getState().setConfirmationState("a1", "pending");
      useChatStore.getState().setCurrentWorkflowDraft({
        action_id: "a1",
        def: {},
        source: "new",
      });

      useChatStore.getState().clearMessages();

      const s = useChatStore.getState();
      expect(s.messages).toEqual([]);
      expect(s.confirmationStates).toEqual({});
      expect(s.currentWorkflowDraft).toBeNull();
      expect(s.isPanelOpen).toBe(true);
    });
  });

  describe("workflow draft (dormant)", () => {
    it("stores and clears a workflow draft", () => {
      const draft = {
        action_id: "act-1",
        def: { name: "Friday mix" },
        source: { workflow_id: "wf-9" } as const,
      };
      useChatStore.getState().setCurrentWorkflowDraft(draft);
      expect(useChatStore.getState().currentWorkflowDraft).toEqual(draft);

      useChatStore.getState().setCurrentWorkflowDraft(null);
      expect(useChatStore.getState().currentWorkflowDraft).toBeNull();
    });
  });
});

describe("conversation helpers", () => {
  beforeEach(() => {
    useChatStore.setState({
      messages: [],
      confirmationStates: {},
      currentWorkflowDraft: null,
    });
  });

  const savePending = (actionId: string, workflowId?: string) => ({
    status: "pending_confirmation",
    action_id: actionId,
    description: "Save it",
    details: {
      mode: workflowId ? "update" : "create",
      definition: { name: "Mix" },
      ...(workflowId ? { workflow_id: workflowId } : {}),
    },
  });

  describe("absorbWorkflowDraft", () => {
    it("absorbs a create proposal as a new-workflow draft", () => {
      absorbWorkflowDraft("save_workflow", savePending("a1"), false);
      expect(useChatStore.getState().currentWorkflowDraft).toEqual({
        action_id: "a1",
        def: { name: "Mix" },
        source: "new",
      });
    });

    it("absorbs an update proposal keyed to its workflow", () => {
      absorbWorkflowDraft("save_workflow", savePending("a2", "wf-1"), false);
      expect(useChatStore.getState().currentWorkflowDraft).toEqual({
        action_id: "a2",
        def: { name: "Mix" },
        source: { workflow_id: "wf-1" },
      });
    });

    it("ignores errors, other tools, and non-pending results", () => {
      absorbWorkflowDraft("save_workflow", savePending("a3"), true);
      absorbWorkflowDraft("describe_node", savePending("a4"), false);
      absorbWorkflowDraft("save_workflow", { status: "confirmed" }, false);
      expect(useChatStore.getState().currentWorkflowDraft).toBeNull();
    });
  });

  describe("findLatestGenerateToolCallId", () => {
    it("returns the newest non-error generate call across messages", () => {
      const messages = [
        {
          id: "m1",
          role: "assistant" as const,
          content: "",
          toolCalls: [{ id: "g1", name: "generate_workflow_def" }],
        },
        {
          id: "m2",
          role: "assistant" as const,
          content: "",
          toolCalls: [
            { id: "g2", name: "generate_workflow_def" },
            { id: "g3", name: "generate_workflow_def", isError: true },
          ],
        },
      ];
      expect(findLatestGenerateToolCallId(messages)).toBe("g2");
      expect(findLatestGenerateToolCallId([])).toBeNull();
    });
  });

  describe("findTriggeringPrompt", () => {
    it("finds the nearest preceding user message", () => {
      const messages = [
        { id: "u1", role: "user" as const, content: "build a mix" },
        { id: "a1", role: "assistant" as const, content: "done" },
        { id: "u2", role: "user" as const, content: "make it longer" },
        { id: "a2", role: "assistant" as const, content: "sure" },
      ];
      expect(findTriggeringPrompt(messages, "a2")).toBe("make it longer");
      expect(findTriggeringPrompt(messages, "a1")).toBe("build a mix");
      expect(findTriggeringPrompt(messages, "missing")).toBe("make it longer");
      expect(findTriggeringPrompt([], "x")).toBeNull();
    });
  });
});
