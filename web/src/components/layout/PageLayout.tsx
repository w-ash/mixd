import { Loader2 } from "lucide-react";
import { lazy, Suspense, useCallback } from "react";

import { ChatEdgeTab } from "#/components/chat/ChatEdgeTab";
import { useChatAvailable } from "#/hooks/useChatAvailable";
import { useIsMobile } from "#/hooks/useIsMobile";
import { useKeyboardShortcut } from "#/hooks/useKeyboardShortcut";
import { useChatStore } from "#/stores/chat-store";

import { MobileShell } from "./MobileShell";
import { RouteOutlet } from "./RouteOutlet";
import { Sidebar } from "./Sidebar";
import { SkipToMainContent } from "./SkipToMainContent";

// Lazy so the chat shell (and streamdown) only load once the panel is opened.
const ChatPanel = lazy(() =>
  import("#/components/chat/ChatPanel").then((m) => ({ default: m.ChatPanel })),
);

export function PageLayout() {
  const isMobile = useIsMobile();
  const isPanelOpen = useChatStore((s) => s.isPanelOpen);
  // Per-user gate (v0.9.0.1): no Anthropic key → no assistant surface at all.
  const { available: chatAvailable } = useChatAvailable();

  const togglePanel = useCallback(() => {
    if (!chatAvailable) return;
    useChatStore.getState().togglePanel();
  }, [chatAvailable]);

  // Cmd/Ctrl+K toggles the assistant panel — inert until a key is connected.
  useKeyboardShortcut(["cmd", "k"], togglePanel);

  if (isMobile) {
    // Mobile reaches chat through the full-screen `/chat` route (ChatPage);
    // the desktop side panel doesn't mount here.
    return <MobileShell />;
  }

  return (
    <div className="flex min-h-screen">
      <SkipToMainContent />
      <Sidebar />
      <main id="main-content" className="flex-1 overflow-y-auto px-page py-8">
        <RouteOutlet />
      </main>
      {chatAvailable &&
        (isPanelOpen ? (
          <div className="hidden w-96 shrink-0 border-l border-border bg-surface-sunken md:flex">
            <Suspense
              fallback={
                <div className="flex flex-1 items-center justify-center">
                  <Loader2 className="size-4 animate-spin text-text-muted" />
                </div>
              }
            >
              <ChatPanel />
            </Suspense>
          </div>
        ) : (
          <ChatEdgeTab />
        ))}
    </div>
  );
}
