import { ErrorBoundary } from "react-error-boundary";
import { Outlet, useLocation } from "react-router";

import { PageErrorFallback } from "./PageErrorFallback";
import { Sidebar } from "./Sidebar";

export function PageLayout() {
  const { pathname } = useLocation();

  return (
    <div className="flex min-h-screen">
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-primary focus:px-4 focus:py-2 focus:text-surface focus:outline-none"
      >
        Skip to content
      </a>
      <Sidebar />
      <main id="main-content" className="flex-1 overflow-y-auto px-page py-8">
        <ErrorBoundary
          FallbackComponent={PageErrorFallback}
          resetKeys={[pathname]}
        >
          <div key={pathname} className="animate-fade-up">
            <Outlet />
          </div>
        </ErrorBoundary>
      </main>
    </div>
  );
}
