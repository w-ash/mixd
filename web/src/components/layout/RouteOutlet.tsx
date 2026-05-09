import { ErrorBoundary } from "react-error-boundary";
import { Outlet, useLocation } from "react-router";

import { PageErrorFallback } from "./PageErrorFallback";

/**
 * Wraps `<Outlet />` with the page-level error boundary and the
 * route-change fade-up animation. Used by both `PageLayout` (desktop)
 * and `MobileShell` (mobile) so the route-transition behavior is
 * defined once.
 *
 * The `key={pathname}` forces the inner subtree to remount on
 * navigation so the fade-up animation re-fires.
 */
export function RouteOutlet() {
  const { pathname } = useLocation();
  return (
    <ErrorBoundary FallbackComponent={PageErrorFallback} resetKeys={[pathname]}>
      <div key={pathname} className="animate-fade-up">
        <Outlet />
      </div>
    </ErrorBoundary>
  );
}
