import type { ReactNode } from "react";

import {
  Dialog,
  DialogContent,
  DialogTrigger,
} from "#/components/ui/dialog";
import { useIsMobile } from "#/hooks/useIsMobile";
import { cn } from "#/lib/utils";

interface ResponsiveDialogProps {
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  trigger?: ReactNode;
  children: ReactNode;
  className?: string;
  showCloseButton?: boolean;
}

const MOBILE_SHEET_CLASS =
  "left-0 right-0 top-auto bottom-0 " +
  "translate-x-0 translate-y-0 " +
  "w-full max-w-full sm:max-w-full max-h-[85svh] " +
  "rounded-b-none rounded-t-2xl " +
  "pb-safe " +
  "data-[state=closed]:slide-out-to-bottom data-[state=closed]:zoom-out-100 " +
  "data-[state=open]:slide-in-from-bottom data-[state=open]:zoom-in-100";

/**
 * Renders as a centered modal Dialog on desktop and a bottom-anchored
 * sheet on mobile, while preserving the same children contract as
 * `<Dialog><DialogContent>...</DialogContent></Dialog>`. Drop-in
 * replacement for that pattern at every dialog caller.
 */
export function ResponsiveDialog({
  open,
  onOpenChange,
  trigger,
  children,
  className,
  showCloseButton,
}: ResponsiveDialogProps) {
  const isMobile = useIsMobile();
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {trigger && <DialogTrigger asChild>{trigger}</DialogTrigger>}
      <DialogContent
        className={cn(isMobile && MOBILE_SHEET_CLASS, className)}
        showCloseButton={showCloseButton}
      >
        {children}
      </DialogContent>
    </Dialog>
  );
}
