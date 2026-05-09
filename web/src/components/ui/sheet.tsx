import type { ReactNode } from "react";

import { useDialogSync } from "#/hooks/useDialogSync";
import { cn } from "#/lib/utils";

interface SheetProps {
  open: boolean;
  onClose: () => void;
  variant?: "sheet" | "fullscreen";
  ariaLabel?: string;
  children: ReactNode;
}

/**
 * Bottom-anchored sheet built on the native `<dialog>` element. Used as
 * the mobile counterpart to `Dialog` (see `ResponsiveDialog`). Honors
 * `pb-safe` so it clears the iPhone home indicator.
 */
export function Sheet({
  open,
  onClose,
  variant = "sheet",
  ariaLabel,
  children,
}: SheetProps) {
  const dialogRef = useDialogSync(open);
  const isFullscreen = variant === "fullscreen";

  return (
    <dialog
      ref={dialogRef}
      onClose={onClose}
      aria-label={ariaLabel}
      className={cn(
        "z-50 m-0 w-full max-w-full bg-surface-elevated p-0 text-text shadow-xl",
        "backdrop:bg-black/40",
        isFullscreen
          ? "fixed inset-0 h-full max-h-full"
          : "fixed inset-auto bottom-0 left-0 right-0 rounded-t-2xl border-t border-border",
      )}
    >
      <div className={isFullscreen ? "h-full" : "px-4 pb-safe pt-3"}>
        {!isFullscreen && (
          <div className="mb-3 mx-auto h-1 w-10 rounded-full bg-text-faint/30" />
        )}
        {children}
      </div>
    </dialog>
  );
}
