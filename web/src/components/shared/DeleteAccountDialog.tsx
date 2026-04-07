import { useState } from "react";

import { ConfirmationDialog } from "#/components/shared/ConfirmationDialog";
import { Input } from "#/components/ui/input";

const CONFIRM_TEXT = "DELETE";

interface DeleteAccountDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  user: { name: string; email: string };
  isPending: boolean;
  onConfirm: () => void;
}

export function DeleteAccountDialog({
  open,
  onOpenChange,
  user,
  isPending,
  onConfirm,
}: DeleteAccountDialogProps) {
  const [confirmText, setConfirmText] = useState("");

  const handleOpenChange = (nextOpen: boolean) => {
    if (!nextOpen) setConfirmText("");
    onOpenChange(nextOpen);
  };

  return (
    <ConfirmationDialog
      open={open}
      onOpenChange={handleOpenChange}
      title="Delete your account"
      description="This action is permanent and cannot be undone. All your data — playlists, listening history, connected services, and settings — will be permanently deleted."
      confirmLabel="Delete account permanently"
      destructive
      isPending={isPending}
      disabled={confirmText !== CONFIRM_TEXT}
      onConfirm={onConfirm}
    >
      <div className="space-y-4">
        <div className="rounded-lg border border-border-muted bg-surface-sunken px-4 py-3">
          <p className="font-display text-sm font-medium">{user.name}</p>
          <p className="font-mono text-xs text-text-muted">{user.email}</p>
        </div>

        <div className="space-y-2">
          <label
            htmlFor="delete-confirm"
            className="font-display text-sm text-text-muted"
          >
            Type{" "}
            <span className="font-mono font-semibold text-text">
              {CONFIRM_TEXT}
            </span>{" "}
            to confirm
          </label>
          <Input
            id="delete-confirm"
            value={confirmText}
            onChange={(e) => setConfirmText(e.target.value)}
            placeholder={CONFIRM_TEXT}
            autoComplete="off"
            spellCheck={false}
          />
        </div>
      </div>
    </ConfirmationDialog>
  );
}
