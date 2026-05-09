import { useEffect, useRef } from "react";

/**
 * Syncs React `open` state with the native `<dialog>` element's
 * imperative API (`showModal()` / `close()`). Use with bottom-sheet
 * and modal-dialog components built on `<dialog>`.
 */
export function useDialogSync(open: boolean) {
  const dialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    else if (!open && dialog.open) dialog.close();
  }, [open]);

  return dialogRef;
}
