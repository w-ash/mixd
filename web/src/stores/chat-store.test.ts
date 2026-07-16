import { beforeEach, describe, expect, it } from "vitest";

import {
  findLatestGenerateToolCallId,
  findLatestSaveProposal,
  findTriggeringPrompt,
  MESSAGE_CAP,
  SOFT_WARN_THRESHOLD,
  selectIsStreaming,
  useChatStore,
} from "./chat-store";

function reset() {
  useChatStore.setState({
    messages: [],
    abortController: null,
    isPanelOpen: false,
    confirmationStates: {},
  });
}

const streaming = () => selectIsStreaming(useChatStore.getState());

describe("chat-store", () => {
  beforeEach(reset);

  it("starts empty with the panel closed", () => {
    const s = useChatStore.getState();
    expect(s.messages).toEqual([]);
    expect(s.isPanelOpen).toBe(false);
    expect(streaming()).toBe(false);
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
      expect(streaming()).toBe(true);
      expect(useChatStore.getState().messages[0]).toMatchObject({
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
      const { messages } = useChatStore.getState();
      expect(messages[0].content).toBe("Hello");
      expect(messages[0].isStreaming).toBe(false);
      expect(streaming()).toBe(false);
    });

    it("records a message error and clears streaming", () => {
      const id = useChatStore.getState().startAssistantMessage();
      useChatStore.getState().setMessageError(id, "E_BOOM", "It broke");
      expect(useChatStore.getState().messages[0].error).toEqual({
        code: "E_BOOM",
        message: "It broke",
      });
      expect(streaming()).toBe(false);
    });

    it("stopStreaming finalizes the trailing streaming message on abort", () => {
      const id = useChatStore.getState().startAssistantMessage();
      useChatStore.getState().setAbortController(new AbortController());
      expect(streaming()).toBe(true);

      useChatStore.getState().stopStreaming();

      expect(useChatStore.getState().messages[0].id).toBe(id);
      expect(streaming()).toBe(false);
      expect(useChatStore.getState().abortController).toBeNull();
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

    it("refuses assistant messages past the cap without flipping streaming", () => {
      for (let i = 0; i < MESSAGE_CAP; i++) {
        useChatStore.getState().addUserMessage(`m${i}`);
      }
      const id = useChatStore.getState().startAssistantMessage();
      expect(id).toBe("");
      expect(useChatStore.getState().messages).toHaveLength(MESSAGE_CAP);
      // No streaming assistant message was created, so nothing is streaming.
      expect(streaming()).toBe(false);
    });
  });

  describe("tool calls", () => {
    it("adds a tool call with its kind and records its result", () => {
      const id = useChatStore.getState().startAssistantMessage();
      useChatStore.getState().addToolCall(id, "tc-1", "search_tracks", "read");
      expect(useChatStore.getState().messages[0].toolCalls?.[0]).toMatchObject({
        id: "tc-1",
        name: "search_tracks",
        kind: "read",
      });

      useChatStore
        .getState()
        .setToolResult(id, "tc-1", "search_tracks", { count: 3 }, false);
      expect(useChatStore.getState().messages[0].toolCalls?.[0]).toMatchObject({
        result: { count: 3 },
        isError: false,
      });
    });

    it("upserts a tool call when a result arrives without a prior tool_start", () => {
      // The synthetic operation_started frame arrives as a tool_result with no
      // preceding tool_start — setToolResult must create the ToolCall so the
      // OperationProgressCard has something to render.
      const id = useChatStore.getState().startAssistantMessage();
      useChatStore
        .getState()
        .setToolResult(
          id,
          "op-1",
          "import_lastfm_history",
          { status: "operation_started", operation_id: "o1" },
          false,
        );

      const call = useChatStore.getState().messages[0].toolCalls?.[0];
      expect(call).toMatchObject({
        id: "op-1",
        name: "import_lastfm_history",
        kind: "write",
        isError: false,
        result: { status: "operation_started", operation_id: "o1" },
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

      useChatStore.getState().clearMessages();

      const s = useChatStore.getState();
      expect(s.messages).toEqual([]);
      expect(s.confirmationStates).toEqual({});
      expect(s.isPanelOpen).toBe(true);
    });
  });
});

describe("conversation helpers", () => {
  beforeEach(() => {
    useChatStore.setState({ messages: [], confirmationStates: {} });
  });

  const savePendingCall = (actionId: string, workflowId?: string) => ({
    id: `s-${actionId}`,
    name: "save_workflow" as const,
    kind: "write" as const,
    result: {
      status: "pending_confirmation",
      action_id: actionId,
      description: "Save it",
      details: {
        mode: workflowId ? "update" : "create",
        definition: { name: "Mix" },
        ...(workflowId ? { workflow_id: workflowId } : {}),
      },
    },
  });

  describe("findLatestSaveProposal", () => {
    it("finds a create proposal", () => {
      const messages = [
        {
          id: "a1",
          role: "assistant" as const,
          content: "",
          toolCalls: [savePendingCall("a1")],
        },
      ];
      expect(findLatestSaveProposal(messages)).toEqual({
        actionId: "a1",
        mode: "create",
      });
    });

    it("finds an update proposal and reads its mode", () => {
      const messages = [
        {
          id: "a1",
          role: "assistant" as const,
          content: "",
          toolCalls: [savePendingCall("a2", "wf-1")],
        },
      ];
      expect(findLatestSaveProposal(messages)).toEqual({
        actionId: "a2",
        mode: "update",
      });
    });

    it("returns the newest proposal across turns", () => {
      const messages = [
        {
          id: "a1",
          role: "assistant" as const,
          content: "",
          toolCalls: [savePendingCall("old")],
        },
        {
          id: "a2",
          role: "assistant" as const,
          content: "",
          toolCalls: [savePendingCall("new")],
        },
      ];
      expect(findLatestSaveProposal(messages)?.actionId).toBe("new");
    });

    it("ignores errored calls, other tools, and non-pending results", () => {
      const messages = [
        {
          id: "a1",
          role: "assistant" as const,
          content: "",
          toolCalls: [
            { ...savePendingCall("errored"), isError: true },
            {
              id: "d1",
              name: "describe_node",
              kind: "read" as const,
              result: { status: "pending_confirmation", action_id: "x" },
            },
            {
              id: "s2",
              name: "save_workflow",
              kind: "write" as const,
              result: { status: "confirmed", action_id: "done" },
            },
          ],
        },
      ];
      expect(findLatestSaveProposal(messages)).toBeNull();
      expect(findLatestSaveProposal([])).toBeNull();
    });
  });

  describe("findLatestGenerateToolCallId", () => {
    it("returns the newest non-error generate call across messages", () => {
      const messages = [
        {
          id: "m1",
          role: "assistant" as const,
          content: "",
          toolCalls: [
            {
              id: "g1",
              name: "generate_workflow_def",
              kind: "read" as const,
            },
          ],
        },
        {
          id: "m2",
          role: "assistant" as const,
          content: "",
          toolCalls: [
            {
              id: "g2",
              name: "generate_workflow_def",
              kind: "read" as const,
            },
            {
              id: "g3",
              name: "generate_workflow_def",
              kind: "read" as const,
              isError: true,
            },
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
