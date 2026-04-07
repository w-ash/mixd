/**
 * Version history panel for a workflow.
 *
 * Shows a list of prior versions with timestamps, change summaries,
 * and actions to view diff or restore a previous version.
 */

import { useQueryClient } from "@tanstack/react-query";
import { Clock, Eye, RotateCcw } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import type {
  WorkflowDefSchema,
  WorkflowVersionSchema,
} from "#/api/generated/model";
import {
  getGetWorkflowApiV1WorkflowsWorkflowIdGetQueryKey,
  useListWorkflowVersionsApiV1WorkflowsWorkflowIdVersionsGet,
  useRevertWorkflowVersionApiV1WorkflowsWorkflowIdVersionsVersionRevertPost,
} from "#/api/generated/workflows/workflows";
import { Button } from "#/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "#/components/ui/dialog";
import { WorkflowDiff } from "#/components/workflow/WorkflowDiff";
import { formatDateTime } from "#/lib/format";
import { useEditorStore } from "#/stores/editor-store";

export function VersionHistory({ workflowId }: { workflowId: string }) {
  const [diffVersion, setDiffVersion] = useState<number | null>(null);
  const queryClient = useQueryClient();
  const loadWorkflow = useEditorStore((s) => s.loadWorkflow);

  const { data: versionsData } =
    useListWorkflowVersionsApiV1WorkflowsWorkflowIdVersionsGet(workflowId);
  const versions = versionsData?.status === 200 ? versionsData.data : [];

  const revertMutation =
    useRevertWorkflowVersionApiV1WorkflowsWorkflowIdVersionsVersionRevertPost();

  const handleRevert = (version: number) => {
    revertMutation.mutate(
      { workflowId, version },
      {
        onSuccess: (res) => {
          if (res.status === 200) {
            const data = res.data as {
              definition: WorkflowDefSchema;
            };
            loadWorkflow(data.definition, workflowId);
            queryClient.invalidateQueries({
              queryKey:
                getGetWorkflowApiV1WorkflowsWorkflowIdGetQueryKey(workflowId),
            });
            toast.success(`Reverted to version ${version}`);
          }
        },
        onError: () => {
          toast.error("Failed to revert");
        },
      },
    );
  };

  if (!Array.isArray(versions) || versions.length === 0) {
    return (
      <div className="flex flex-col items-center gap-2 py-8">
        <Clock className="size-6 text-text-faint" />
        <p className="font-body text-sm text-text-faint">No version history</p>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {(versions as WorkflowVersionSchema[]).map((v) => (
        <div
          key={v.version}
          className="flex items-center justify-between rounded border-l-2 border-border bg-surface-elevated px-3 py-2"
        >
          <div className="min-w-0 flex-1">
            <div className="flex items-baseline gap-2">
              <span className="font-mono text-xs font-medium text-text">
                v{v.version}
              </span>
              <span className="font-mono text-[10px] text-text-faint">
                {formatDateTime(v.created_at)}
              </span>
            </div>
            {v.change_summary && (
              <p className="mt-0.5 truncate font-body text-[11px] text-text-muted">
                {v.change_summary}
              </p>
            )}
          </div>
          <div className="flex items-center gap-1 pl-2">
            <Dialog
              open={diffVersion === v.version}
              onOpenChange={(open) => setDiffVersion(open ? v.version : null)}
            >
              <DialogTrigger asChild>
                <Button variant="ghost" size="icon-xs" title="View diff">
                  <Eye className="size-3" />
                </Button>
              </DialogTrigger>
              <DialogContent className="max-w-5xl">
                <DialogHeader>
                  <DialogTitle>Version {v.version} vs Current</DialogTitle>
                </DialogHeader>
                <WorkflowDiff workflowId={workflowId} version={v.version} />
              </DialogContent>
            </Dialog>
            <Button
              variant="ghost"
              size="icon-xs"
              title="Restore this version"
              onClick={() => handleRevert(v.version)}
            >
              <RotateCcw className="size-3" />
            </Button>
          </div>
        </div>
      ))}
    </div>
  );
}
