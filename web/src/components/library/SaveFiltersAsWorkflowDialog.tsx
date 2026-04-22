import { Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router";

import { useCreateWorkflowApiV1WorkflowsPost } from "#/api/generated/workflows/workflows";
import { Button } from "#/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "#/components/ui/dialog";
import { Input } from "#/components/ui/input";
import type { LibraryFilterState } from "#/lib/filters-to-workflow";
import {
  filtersToWorkflowDef,
  summarizeFilters,
} from "#/lib/filters-to-workflow";
import { toasts } from "#/lib/toasts";

interface SaveFiltersAsWorkflowDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  filters: LibraryFilterState;
  /** Whether the destination (saved) workflow will start from liked tracks
   * instead of the user's full library — used to surface the implicit scope
   * narrowing to the user before they commit. */
  narrowsToLiked?: boolean;
}

/**
 * Name-and-save dialog that turns the current Library filter state into a
 * persisted workflow. On success, navigates to the editor pre-populated with
 * the newly-created workflow (route: `/workflows/:id/edit`).
 *
 * Error handling: the create mutation's error is surfaced inline in the
 * dialog footer. The user stays on the dialog and can retry — consistent
 * with ConfirmationDialog-style interactions elsewhere in the app.
 */
export function SaveFiltersAsWorkflowDialog({
  open,
  onOpenChange,
  filters,
  narrowsToLiked = false,
}: SaveFiltersAsWorkflowDialogProps) {
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");

  // Reset form state every time the dialog opens so old input doesn't linger.
  useEffect(() => {
    if (open) {
      setName("");
      setDescription("");
    }
  }, [open]);

  const mutation = useCreateWorkflowApiV1WorkflowsPost();

  const trimmedName = name.trim();
  const canSave = trimmedName.length > 0 && !mutation.isPending;

  const handleSave = () => {
    if (!canSave) return;
    const definition = filtersToWorkflowDef(filters, {
      name: trimmedName,
      description: description.trim() || undefined,
    });
    mutation.mutate(
      { data: { definition } },
      {
        onSuccess: (response) => {
          if (response.status === 201) {
            onOpenChange(false);
            toasts.success(`Saved "${trimmedName}"`);
            navigate(`/workflows/${response.data.id}/edit`);
          }
        },
      },
    );
  };

  const filterSummary = summarizeFilters(filters);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Save as Workflow</DialogTitle>
          <DialogDescription>
            Turn your current Library filters into a reusable workflow you can
            schedule or re-run from the workflow editor.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-2">
          <div className="space-y-1.5">
            <label
              htmlFor="save-workflow-name"
              className="block font-display text-xs uppercase tracking-wider text-text-muted"
            >
              Name
            </label>
            <Input
              id="save-workflow-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="My Starred Chill"
              autoFocus
            />
          </div>

          <div className="space-y-1.5">
            <label
              htmlFor="save-workflow-description"
              className="block font-display text-xs uppercase tracking-wider text-text-muted"
            >
              Description (optional)
            </label>
            <Input
              id="save-workflow-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder={filterSummary}
            />
          </div>

          <p className="text-xs text-text-muted">
            {filterSummary}.{" "}
            {narrowsToLiked
              ? "The saved workflow will start from your liked tracks. Edit in the workflow editor to broaden the source."
              : "Edit the graph in the workflow editor after saving to add a schedule, change the output destination, or adjust nodes."}
          </p>

          {mutation.isError && (
            <p
              role="alert"
              className="text-xs text-destructive"
              aria-live="polite"
            >
              Couldn't save workflow. Please try again.
            </p>
          )}
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={mutation.isPending}
          >
            Cancel
          </Button>
          <Button onClick={handleSave} disabled={!canSave}>
            {mutation.isPending && (
              <Loader2 className="mr-1.5 size-3.5 animate-spin" />
            )}
            Save & open editor
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
