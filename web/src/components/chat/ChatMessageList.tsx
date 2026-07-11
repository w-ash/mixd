import { useCallback } from "react";

import type { ChatMessage as ChatMessageType } from "#/stores/chat-store";
import { useChatStore } from "#/stores/chat-store";

import { ChatMessage } from "./ChatMessage";

export function ChatMessageList({
  messages,
  onConfirm,
  onCancel,
  onSendMessage,
}: {
  messages: ChatMessageType[];
  onConfirm?: (actionId: string) => void;
  onCancel?: (actionId: string) => void;
  onSendMessage?: (text: string) => void;
}) {
  const confirmationStates = useChatStore((s) => s.confirmationStates);
  const lastMessageRef = useCallback((node: HTMLDivElement | null) => {
    node?.scrollIntoView({ behavior: "smooth" });
  }, []);

  return (
    <div className="flex flex-1 flex-col gap-3 overflow-y-auto px-4 py-4">
      {messages.map((m, i) => (
        <div
          key={m.id}
          ref={i === messages.length - 1 ? lastMessageRef : undefined}
        >
          <ChatMessage
            message={m}
            confirmationStates={confirmationStates}
            onConfirm={onConfirm}
            onCancel={onCancel}
            onSendMessage={onSendMessage}
          />
        </div>
      ))}
    </div>
  );
}
