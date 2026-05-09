import { MobileBottomNav } from "#/components/layout/MobileBottomNav";
import { RouteOutlet } from "#/components/layout/RouteOutlet";
import { SkipToMainContent } from "#/components/layout/SkipToMainContent";
import { MixdLogo } from "#/components/shared/MixdLogo";

export function MobileShell() {
  return (
    <div className="flex min-h-svh flex-col">
      <SkipToMainContent />

      <header className="sticky top-0 z-30 flex h-14 items-center justify-center border-b border-border bg-surface-sunken px-4">
        <MixdLogo />
      </header>

      <main
        id="main-content"
        className="flex-1 overflow-y-auto px-4 py-6 pb-24"
      >
        <RouteOutlet />
      </main>

      <MobileBottomNav />
    </div>
  );
}
