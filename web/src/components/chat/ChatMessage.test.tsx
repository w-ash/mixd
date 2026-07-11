import { fireEvent } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ChatMessage as ChatMessageType } from "#/stores/chat-store";
import { renderWithProviders, screen } from "#/test/test-utils";

import { ChatMessage } from "./ChatMessage";

function makeMessage(overrides: Partial<ChatMessageType>): ChatMessageType {
  return {
    id: "msg-1",
    role: "assistant",
    content: "",
    ...overrides,
  };
}

describe("ChatMessage", () => {
  describe("user message", () => {
    it("renders text content", () => {
      renderWithProviders(
        <ChatMessage
          message={makeMessage({ role: "user", content: "Hello there" })}
        />,
      );
      expect(screen.getByText("Hello there")).toBeInTheDocument();
    });

    it("renders as plain text (no markdown parsing)", () => {
      renderWithProviders(
        <ChatMessage
          message={makeMessage({ role: "user", content: "**bold text**" })}
        />,
      );
      expect(screen.getByText("**bold text**")).toBeInTheDocument();
    });
  });

  describe("assistant message", () => {
    it("renders text content", () => {
      renderWithProviders(
        <ChatMessage
          message={makeMessage({ content: "Here is your answer." })}
        />,
      );
      expect(screen.getByText("Here is your answer.")).toBeInTheDocument();
    });

    it("renders bold text via Streamdown", () => {
      renderWithProviders(
        <ChatMessage
          message={makeMessage({ content: "This is **important** info." })}
        />,
      );
      expect(screen.getByText("important")).toBeInTheDocument();
    });

    it("renders a markdown table", () => {
      const tableMarkdown = [
        "| Track | Plays |",
        "|---|---|",
        "| Teardrop | 42 |",
        "| Angel | 17 |",
      ].join("\n");

      renderWithProviders(
        <ChatMessage message={makeMessage({ content: tableMarkdown })} />,
      );
      expect(screen.getByRole("table")).toBeInTheDocument();
      expect(screen.getByText("Teardrop")).toBeInTheDocument();
      expect(screen.getByText("Angel")).toBeInTheDocument();
    });

    it("shows the thinking indicator when streaming with no content", () => {
      renderWithProviders(
        <ChatMessage
          message={makeMessage({ content: "", isStreaming: true })}
        />,
      );
      expect(screen.getByLabelText("Thinking")).toBeInTheDocument();
    });

    it("hides the thinking indicator once content arrives", () => {
      renderWithProviders(
        <ChatMessage
          message={makeMessage({
            content: "Starting response...",
            isStreaming: true,
          })}
        />,
      );
      expect(screen.queryByLabelText("Thinking")).not.toBeInTheDocument();
      expect(screen.getByText("Starting response...")).toBeInTheDocument();
    });

    it("renders the error state", () => {
      renderWithProviders(
        <ChatMessage
          message={makeMessage({
            error: { code: "TOOL_ERROR", message: "Tool failed" },
          })}
        />,
      );
      expect(screen.getByText("Tool failed")).toBeInTheDocument();
    });
  });

  describe("copy button", () => {
    let writeText: ReturnType<typeof vi.fn>;

    beforeEach(() => {
      writeText = vi.fn(() => Promise.resolve());
      Object.defineProperty(navigator, "clipboard", {
        value: { writeText },
        configurable: true,
        writable: true,
      });
      vi.useFakeTimers({ shouldAdvanceTime: true });
    });

    afterEach(() => {
      vi.useRealTimers();
    });

    it("is not rendered for user messages", () => {
      renderWithProviders(
        <ChatMessage
          message={makeMessage({ role: "user", content: "Hello" })}
        />,
      );
      expect(
        screen.queryByRole("button", { name: /copy message/i }),
      ).not.toBeInTheDocument();
    });

    it("is not rendered while streaming", () => {
      renderWithProviders(
        <ChatMessage
          message={makeMessage({ content: "partial", isStreaming: true })}
        />,
      );
      expect(
        screen.queryByRole("button", { name: /copy message/i }),
      ).not.toBeInTheDocument();
    });

    it("copies content to the clipboard and flashes Copied", async () => {
      renderWithProviders(
        <ChatMessage message={makeMessage({ content: "**hello** world" })} />,
      );

      fireEvent.click(screen.getByRole("button", { name: /copy message/i }));
      await vi.waitFor(() =>
        expect(writeText).toHaveBeenCalledWith("**hello** world"),
      );
      await vi.waitFor(() =>
        expect(
          screen.getByRole("button", { name: /copied/i }),
        ).toBeInTheDocument(),
      );

      vi.advanceTimersByTime(1600);
      await vi.waitFor(() =>
        expect(
          screen.getByRole("button", { name: /copy message/i }),
        ).toBeInTheDocument(),
      );
    });
  });

  describe("tool calls", () => {
    it("renders a tool call indicator", () => {
      renderWithProviders(
        <ChatMessage
          message={makeMessage({
            content: "Let me check that.",
            toolCalls: [{ id: "tc-1", name: "search_tracks" }],
          })}
        />,
      );
      expect(screen.getByText(/looking up tracks/i)).toBeInTheDocument();
    });

    it("renders a generic result card when a result is present", () => {
      renderWithProviders(
        <ChatMessage
          message={makeMessage({
            content: "Here are your tracks.",
            toolCalls: [
              {
                id: "tc-1",
                name: "search_tracks",
                result: { total_count: 3, showing: 3 },
              },
            ],
          })}
        />,
      );
      expect(screen.getByText("total count")).toBeInTheDocument();
      expect(screen.getByText(/checked tracks/i)).toBeInTheDocument();
    });

    it("does not render a result card for error results", () => {
      renderWithProviders(
        <ChatMessage
          message={makeMessage({
            content: "Something went wrong.",
            toolCalls: [
              {
                id: "tc-1",
                name: "search_tracks",
                result: { error: "failed" },
                isError: true,
              },
            ],
          })}
        />,
      );
      expect(screen.getByText(/checked tracks/i)).toBeInTheDocument();
      expect(screen.queryByText("total count")).not.toBeInTheDocument();
    });
  });
});
