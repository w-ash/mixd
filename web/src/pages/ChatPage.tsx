import { useEffect } from "react";
import { useNavigate } from "react-router";

import { ChatPanel } from "#/components/chat/ChatPanel";
import { useChatAvailable } from "#/hooks/useChatAvailable";
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
  const { available, isLoading } = useChatAvailable();

  useEffect(() => {
    // Wait for the per-user gate before deciding — otherwise a desktop
    // deep-link/refresh redirects home while `available` is still false and the
    // panel never opens.
    if (isLoading) return;
    if (!isMobile) {
      // Desktop deep-link: open the side panel and bounce home.
      if (available) useChatStore.getState().setPanelOpen(true);
      navigate("/", { replace: true });
      return;
    }
    // Mobile with no key: this route shouldn't be reachable (the nav tab is
    // hidden), but guard direct navigation by sending them to connect a key.
    if (!available) {
      navigate("/settings/assistant", { replace: true });
    }
  }, [isMobile, available, isLoading, navigate]);

  if (!isMobile || !available) return null;

  return (
    <div className={CHAT_HEIGHT_CLASS}>
      <ChatPanel fullScreen />
    </div>
  );
}
