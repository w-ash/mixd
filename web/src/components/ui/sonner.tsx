import { Toaster as Sonner, type ToasterProps } from "sonner";

import { useTheme } from "#/contexts/ThemeContext";
import { useIsMobile } from "#/hooks/useIsMobile";

/**
 * Global toast renderer. Mounted once at the app root.
 *
 * Defaults tuned for mixd's dark editorial aesthetic:
 *   - desktop: bottom-right, out of the way of the sidebar + main content
 *   - mobile: top-center, so toasts sit above the bottom nav and remain
 *     tappable for deep-link actions
 *   - richColors: success/error/info/warning get semantic tinting
 *   - closeButton: long-duration toasts must be dismissible
 *   - expand: stack vertically so multi-operation flows stay legible
 *   - duration: 5s — long enough to read, short enough not to nag
 */
export function Toaster(props: ToasterProps) {
  const { resolvedTheme } = useTheme();
  const isMobile = useIsMobile();
  return (
    <Sonner
      theme={resolvedTheme}
      className="toaster group"
      position={isMobile ? "top-center" : "bottom-right"}
      richColors
      closeButton
      expand
      duration={5000}
      style={
        {
          "--normal-bg": "var(--popover)",
          "--normal-text": "var(--popover-foreground)",
          "--normal-border": "var(--border)",
          "--border-radius": "var(--radius)",
        } as React.CSSProperties
      }
      {...props}
    />
  );
}
