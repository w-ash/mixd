import { Loader2 } from "lucide-react";
import { lazy, Suspense, useCallback } from "react";

import { ChatEdgeTab } from "#/components/chat/ChatEdgeTab";
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

  const togglePanel = useCallback(() => {
    useChatStore.getState().togglePanel();
  }, []);

  // Cmd/Ctrl+K toggles the assistant panel from anywhere in the app.
  useKeyboardShortcut(["cmd", "k"], togglePanel);

  if (isMobile) {
    // Mobile keeps its full-screen chat route for a later phase — the desktop
    // side panel doesn't mount here.
    return <MobileShell />;
  }

  return (
    <div className="flex min-h-screen">
      <SkipToMainContent />
      <Sidebar />
      <main id="main-content" className="flex-1 overflow-y-auto px-page py-8">
        <RouteOutlet />
      </main>
      {isPanelOpen ? (
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
      )}
    </div>
  );
}
