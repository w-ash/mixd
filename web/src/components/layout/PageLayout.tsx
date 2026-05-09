import { useIsMobile } from "#/hooks/useIsMobile";

import { MobileShell } from "./MobileShell";
import { RouteOutlet } from "./RouteOutlet";
import { Sidebar } from "./Sidebar";
import { SkipToMainContent } from "./SkipToMainContent";

export function PageLayout() {
  const isMobile = useIsMobile();

  if (isMobile) {
    return <MobileShell />;
  }

  return (
    <div className="flex min-h-screen">
      <SkipToMainContent />
      <Sidebar />
      <main id="main-content" className="flex-1 overflow-y-auto px-page py-8">
        <RouteOutlet />
      </main>
    </div>
  );
}
