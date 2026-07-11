import { act, fireEvent } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { type ChatSSECallbacks, sendChatMessage } from "#/api/chat-sse";
import type { ChatMessage } from "#/stores/chat-store";
import { useChatStore } from "#/stores/chat-store";
import { renderWithProviders, screen } from "#/test/test-utils";

import { ChatPanel } from "./ChatPanel";

vi.mock("#/api/chat-sse", () => ({ sendChatMessage: vi.fn() }));
const mockSend = vi.mocked(sendChatMessage);

/** The callbacks the panel handed to the (mocked) SSE client on the last send. */
function lastCallbacks(): ChatSSECallbacks {
  return mockSend.mock.calls.at(-1)?.[1] as ChatSSECallbacks;
}

function seedMessages(items: Partial<ChatMessage>[]) {
  const messages: ChatMessage[] = items.map((item, i) => ({
    id: `m-${i}`,
    role: "user",
    content: `msg ${i}`,
    ...item,
  }));
  useChatStore.setState({ messages });
}

function resetStore() {
  useChatStore.setState({
    messages: [],
    isStreaming: false,
    abortController: null,
    isPanelOpen: false,
    confirmationStates: {},
    currentWorkflowDraft: null,
  });
}

describe("ChatPanel", () => {
  beforeEach(() => {
    resetStore();
    mockSend.mockReset();
    mockSend.mockResolvedValue(undefined);
  });

  it("opens an SSE stream via sendChatMessage on submit", () => {
    renderWithProviders(<ChatPanel />);
    fireEvent.click(
      screen.getByRole("button", { name: /friday-night dinner playlist/i }),
    );

    expect(mockSend).toHaveBeenCalledTimes(1);
    const [messages, , signal, confirmation, effort] = mockSend.mock.calls[0];
    expect(messages.at(-1)).toMatchObject({ role: "user" });
    // The empty assistant placeholder is not sent to the model.
    expect(messages.every((m) => m.content !== "")).toBe(true);
    expect(signal).toBeInstanceOf(AbortSignal);
    expect(confirmation).toBeUndefined();
    expect(effort).toBe("high"); // "standard" default -> high
  });

  it("passes the current workflow id when the editor route is open", () => {
    renderWithProviders(<ChatPanel />, {
      routerProps: { initialEntries: ["/workflows/abc-123/edit"] },
    });
    fireEvent.click(
      screen.getByRole("button", { name: /friday-night dinner playlist/i }),
    );

    const currentWorkflowId = mockSend.mock.calls.at(-1)?.[5];
    expect(currentWorkflowId).toBe("abc-123");
  });

  it("sends no workflow id outside the editor route", () => {
    renderWithProviders(<ChatPanel />, {
      routerProps: { initialEntries: ["/"] },
    });
    fireEvent.click(
      screen.getByRole("button", { name: /friday-night dinner playlist/i }),
    );

    const currentWorkflowId = mockSend.mock.calls.at(-1)?.[5];
    expect(currentWorkflowId).toBeUndefined();
  });

  it("renders streamed tokens into the assistant message", () => {
    renderWithProviders(<ChatPanel />);
    fireEvent.click(
      screen.getByRole("button", { name: /friday-night dinner playlist/i }),
    );

    const cb = lastCallbacks();
    act(() => {
      cb.onToken("Here's");
      cb.onToken(" a mix");
      cb.onDone();
    });

    const last = useChatStore.getState().messages.at(-1);
    expect(last).toMatchObject({
      role: "assistant",
      content: "Here's a mix",
      isStreaming: false,
    });
    expect(useChatStore.getState().abortController).toBeNull();
  });

  it("surfaces a typed stream error on the assistant message", () => {
    renderWithProviders(<ChatPanel />);
    fireEvent.click(
      screen.getByRole("button", { name: /friday-night dinner playlist/i }),
    );

    act(() => lastCallbacks().onError("RATE_LIMIT_EXCEEDED", "slow down"));

    expect(useChatStore.getState().messages.at(-1)?.error).toMatchObject({
      code: "RATE_LIMIT_EXCEEDED",
    });
  });

  it("hides the controls row when there are no messages and no limit error", () => {
    renderWithProviders(<ChatPanel />);
    expect(
      screen.queryByRole("button", { name: /new conversation/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /regenerate/i }),
    ).not.toBeInTheDocument();
  });

  it("shows suggested questions and sends one on click", () => {
    renderWithProviders(<ChatPanel />);
    fireEvent.click(
      screen.getByRole("button", { name: /friday-night dinner playlist/i }),
    );

    const { messages } = useChatStore.getState();
    expect(messages[0]).toMatchObject({ role: "user" });
    expect(messages[1]).toMatchObject({ role: "assistant" });
  });

  it("shows New conversation once messages exist and clears them on click", () => {
    seedMessages([
      { role: "user", content: "Hi" },
      { role: "assistant", content: "Hello" },
    ]);
    renderWithProviders(<ChatPanel />);

    fireEvent.click(screen.getByRole("button", { name: /new conversation/i }));
    expect(useChatStore.getState().messages).toEqual([]);
  });

  it("hides Regenerate when the last message is from the user", () => {
    seedMessages([{ role: "user", content: "Hi" }]);
    renderWithProviders(<ChatPanel />);
    expect(
      screen.queryByRole("button", { name: /regenerate/i }),
    ).not.toBeInTheDocument();
  });

  it("regenerates by replacing the last assistant message", () => {
    seedMessages([
      { role: "user", content: "Hi" },
      { role: "assistant", content: "wrong", id: "assistant-1" },
    ]);
    renderWithProviders(<ChatPanel />);

    fireEvent.click(screen.getByRole("button", { name: /regenerate/i }));

    const { messages } = useChatStore.getState();
    expect(messages).toHaveLength(2);
    expect(messages[1].role).toBe("assistant");
    expect(messages[1].id).not.toBe("assistant-1");
  });

  it("Escape closes the panel", () => {
    useChatStore.setState({ isPanelOpen: true });
    renderWithProviders(<ChatPanel />);

    fireEvent.keyDown(window, { key: "Escape" });
    expect(useChatStore.getState().isPanelOpen).toBe(false);
  });

  it("warns softly at 45 messages while sending still works", () => {
    seedMessages(
      Array.from({ length: 45 }, (_, i) => ({
        role: i % 2 === 0 ? "user" : "assistant",
        content: `msg ${i}`,
      })),
    );
    renderWithProviders(<ChatPanel />);

    expect(screen.getByRole("alert")).toHaveTextContent(/getting long/i);
    expect(screen.queryByText(/conversation is full/i)).not.toBeInTheDocument();

    const textarea = screen.getByPlaceholderText(/ask about your music/i);
    fireEvent.change(textarea, { target: { value: "one more" } });
    fireEvent.keyDown(textarea, { key: "Enter" });

    expect(useChatStore.getState().messages.length).toBeGreaterThan(45);
  });

  it("surfaces the limit error and blocks sending when the conversation is full", () => {
    seedMessages(
      Array.from({ length: 50 }, (_, i) => ({
        role: i % 2 === 0 ? "user" : "assistant",
        content: `msg ${i}`,
      })),
    );
    renderWithProviders(<ChatPanel />);

    const textarea = screen.getByPlaceholderText(/ask about your music/i);
    fireEvent.change(textarea, { target: { value: "one more question" } });
    fireEvent.keyDown(textarea, { key: "Enter" });

    expect(screen.getByRole("alert")).toHaveTextContent(
      /conversation is full/i,
    );
    expect(useChatStore.getState().messages).toHaveLength(50);

    const newButton = screen.getByRole("button", { name: /new conversation/i });
    expect(newButton.className).toMatch(/bg-primary/);

    fireEvent.click(newButton);
    expect(useChatStore.getState().messages).toEqual([]);
    expect(screen.queryByText(/conversation is full/i)).not.toBeInTheDocument();
  });
});
