import { useEffect, useState } from "react";

const MOBILE_QUERY = "(max-width: 1023px)";

/**
 * Returns true when viewport is below mixd's `lg:` breakpoint (1024px).
 *
 * Drives the layout-shell switch (MobileShell vs Sidebar+main) and the
 * Dialog-vs-Sheet decision in ResponsiveDialog.
 */
export function useIsMobile(): boolean {
  const [isMobile, setIsMobile] = useState(
    () => window.matchMedia(MOBILE_QUERY).matches,
  );

  useEffect(() => {
    const mql = window.matchMedia(MOBILE_QUERY);
    const handler = (e: MediaQueryListEvent) => setIsMobile(e.matches);
    mql.addEventListener("change", handler);
    return () => mql.removeEventListener("change", handler);
  }, []);

  return isMobile;
}
