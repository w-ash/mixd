/**
 * Whether the viewport is below mixd's `lg:` breakpoint (1024px).
 *
 * Drives the layout-shell switch (MobileShell vs Sidebar+main) and the
 * Dialog-vs-Sheet decision in ResponsiveDialog.
 *
 * The MediaQueryList is created inside `subscribe`/`getSnapshot` rather than
 * held in a module constant, so a test can swap `window.matchMedia` before the
 * hook runs and get the viewport it asked for.
 */

import { useSyncExternalStore } from "react";

const MOBILE_QUERY = "(max-width: 1023px)";

function subscribe(onChange: () => void): () => void {
  const mql = window.matchMedia(MOBILE_QUERY);
  mql.addEventListener("change", onChange);
  return () => mql.removeEventListener("change", onChange);
}

const getSnapshot = () => window.matchMedia(MOBILE_QUERY).matches;

/** Server render has no viewport; assume desktop, as the CSS does. */
const getServerSnapshot = () => false;

export function useIsMobile(): boolean {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}
