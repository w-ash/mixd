import { useEffect } from "react";
import { useNavigate } from "react-router";

import { ChatPanel } from "#/components/chat/ChatPanel";
import { useIsMobile } from "#/hooks/useIsMobile";
import { useChatStore } from "#/stores/chat-store";

// MobileShell chrome to subtract from the viewport so the message list
// scrolls internally instead of the page: header is h-14 (56px) and main's
// vertical padding is py-6 top (24px) + pb-24 bottom (96px) = 176px = 11rem.
const CHAT_HEIGHT_CLASS = "h-[calc(100svh-11rem)]";

/**
 * Full-screen chat route for mobile. Desktop has no dedicated route — the
 * assistant lives in PageLayout's side panel instead, so landing here on a
 * wide viewport just opens that panel and bounces back to `/`.
 */
export function ChatPage() {
  const isMobile = useIsMobile();
  const navigate = useNavigate();

  useEffect(() => {
    if (!isMobile) {
      useChatStore.getState().setPanelOpen(true);
      navigate("/", { replace: true });
    }
  }, [isMobile, navigate]);

  if (!isMobile) return null;

  return (
    <div className={CHAT_HEIGHT_CLASS}>
      <ChatPanel fullScreen />
    </div>
  );
}
