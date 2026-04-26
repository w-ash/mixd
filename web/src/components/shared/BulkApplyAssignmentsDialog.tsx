import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router";

import { useApplyBulkAssignmentsApiV1PlaylistAssignmentsApplyBulkPost } from "#/api/generated/playlist-assignments/playlist-assignments";
import { getListTagsApiV1TagsGetQueryKey } from "#/api/generated/tags/tags";
import { useOperationProgress } from "#/hooks/useOperationProgress";
import { toasts } from "#/lib/toasts";

import { ConfirmationDialog } from "./ConfirmationDialog";
import { OperationProgress } from "./OperationProgress";

interface BulkApplyAssignmentsDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * Re-applies every active playlist→tag/preference assignment for the
 * user. Long-running because it walks each assigned connector playlist's
 * full track list; runs in the background via SSE so the user can leave
 * the page and find the audit row in Settings → Import History later.
 *
 * Three inline phases share one dialog shell:
 *
 * 1. **Compose**: explanation + Apply button.
 * 2. **Running**: ``<OperationProgress>`` (bar + ETA + phase message).
 * 3. **Done**: terminal toast fires once; Close button replaces Apply.
 */
export function BulkApplyAssignmentsDialog({
  open,
  onOpenChange,
}: BulkApplyAssignmentsDialogProps) {
  const [operationId, setOperationId] = useState<string | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const navigate = useNavigate();

  const applyMut = useApplyBulkAssignmentsApiV1PlaylistAssignmentsApplyBulkPost(
    {
      mutation: {
        onSuccess: (response) => {
          if (response.status !== 202) return;
          setOperationId(response.data.operation_id);
          setRunId(response.data.run_id ?? null);
        },
        meta: { errorLabel: "Failed to start bulk apply" },
      },
    },
  );

  const { progress } = useOperationProgress(operationId, {
    invalidateKeys: [getListTagsApiV1TagsGetQueryKey()],
  });

  const isTerminal =
    progress !== null &&
    (progress.status === "completed" ||
      progress.status === "failed" ||
      progress.status === "cancelled");

  const toastedForOpIdRef = useRef<string | null>(null);
  useEffect(() => {
    if (!isTerminal || !operationId || progress === null) return;
    if (toastedForOpIdRef.current === operationId) return;
    toastedForOpIdRef.current = operationId;

    toasts.runCompleted({
      operationType: "apply_assignments_bulk",
      counts: {},
      issueCount: 0,
      runId,
      failed: progress.status !== "completed",
      onNavigate: navigate,
    });
  }, [isTerminal, operationId, progress, runId, navigate]);

  const handleOpenChange = (nextOpen: boolean) => {
    onOpenChange(nextOpen);
    if (!nextOpen) {
      setOperationId(null);
      setRunId(null);
      toastedForOpIdRef.current = null;
    }
  };

  const running = operationId !== null && !isTerminal;
  const confirmLabel = isTerminal ? "Close" : running ? "Applying…" : "Apply";

  const handleConfirm = () => {
    if (isTerminal) {
      handleOpenChange(false);
      return;
    }
    if (!running) {
      applyMut.mutate();
    }
  };

  return (
    <ConfirmationDialog
      open={open}
      onOpenChange={handleOpenChange}
      title="Apply all assignments"
      description={
        operationId === null
          ? "Re-runs the metadata engine on every active playlist→tag and playlist→preference assignment. Tracks added since the last apply will receive the assignment's tag or preference; tracks removed from the playlist will have their assignment-sourced metadata cleared. Manual tags and preferences are preserved."
          : undefined
      }
      confirmLabel={confirmLabel}
      isPending={running || applyMut.isPending}
      disabled={running}
      onConfirm={handleConfirm}
    >
      {progress !== null && <OperationProgress progress={progress} />}
    </ConfirmationDialog>
  );
}
