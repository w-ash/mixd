import { MessageCircle } from "lucide-react";

import { useChatStore } from "#/stores/chat-store";

/**
 * The collapsed-state affordance: a slim vertical rail on the right edge that
 * opens the assistant panel. Desktop-only (md+) — mobile gets a full-screen
 * chat route in a later phase.
 */
export function ChatEdgeTab() {
  const togglePanel = useChatStore((s) => s.togglePanel);

  return (
    <button
      type="button"
      onClick={togglePanel}
      className="hidden w-10 shrink-0 cursor-pointer items-center justify-center border-l border-border bg-surface-sunken transition-colors hover:bg-surface-elevated md:flex"
      aria-label="Open chat assistant"
    >
      <MessageCircle className="size-[18px] text-text-muted" />
    </button>
  );
}
