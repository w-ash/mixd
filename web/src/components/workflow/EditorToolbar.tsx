/**
 * Editor toolbar with save, undo/redo, auto-layout, and workflow metadata.
 *
 * Save flow: serialize store → POST (new) or PATCH (edit) → update store state.
 * Keyboard shortcuts registered via useEffect in WorkflowEditor.
 */

import { useReactFlow } from "@xyflow/react";
import {
  ArrowLeft,
  Clock,
  Eye,
  LayoutGrid,
  Maximize2,
  Play,
  Redo2,
  Save,
  Trash2,
  Undo2,
} from "lucide-react";
import { useCallback, useEffect, useRef } from "react";
import { useNavigate } from "react-router";
import { toast } from "sonner";
import type { WorkflowTaskDefSchema } from "@/api/generated/model";
import {
  useCreateWorkflowApiV1WorkflowsPost,
  useUpdateWorkflowApiV1WorkflowsWorkflowIdPatch,
} from "@/api/generated/workflows/workflows";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { VersionHistory } from "@/components/workflow/VersionHistory";
import { layoutWorkflow } from "@/lib/workflow-layout";
import { useEditorStore } from "@/stores/editor-store";

export function EditorToolbar() {
  const navigate = useNavigate();
  const { fitView } = useReactFlow();

  const workflowId = useEditorStore((s) => s.workflowId);
  const workflowName = useEditorStore((s) => s.workflowName);
  const isDirty = useEditorStore((s) => s.isDirty);
  const setName = useEditorStore((s) => s.setName);
  const toWorkflowDef = useEditorStore((s) => s.toWorkflowDef);
  const resetDirty = useEditorStore((s) => s.resetDirty);
  const undo = useEditorStore((s) => s.undo);
  const redo = useEditorStore((s) => s.redo);
  const canUndo = useEditorStore((s) => s.past.length > 0);
  const canRedo = useEditorStore((s) => s.future.length > 0);
  const removeSelected = useEditorStore((s) => s.removeSelected);
  const setNodes = useEditorStore((s) => s.setNodes);
  const setEdges = useEditorStore((s) => s.setEdges);
  const hasNodes = useEditorStore((s) => s.nodes.length > 0);

  const nameInputRef = useRef<HTMLInputElement>(null);

  const createMutation = useCreateWorkflowApiV1WorkflowsPost();
  const updateMutation = useUpdateWorkflowApiV1WorkflowsWorkflowIdPatch();
  const isSaving = createMutation.isPending || updateMutation.isPending;

  const { mutate: createWorkflow } = createMutation;
  const { mutate: updateWorkflow } = updateMutation;

  const handleSave = useCallback(() => {
    const def = toWorkflowDef();

    if (workflowId === null) {
      createWorkflow(
        { data: { definition: def } },
        {
          onSuccess: (res) => {
            if (res.status === 201) {
              const newId = (res.data as { id: string }).id;
              resetDirty();
              toast.success("Workflow created");
              navigate(`/workflows/${newId}/edit`, { replace: true });
            }
          },
          onError: () => {
            toast.error("Failed to save workflow");
          },
        },
      );
    } else {
      updateWorkflow(
        { workflowId, data: { definition: def } },
        {
          onSuccess: () => {
            resetDirty();
            toast.success("Workflow saved");
          },
          onError: () => {
            toast.error("Failed to save workflow");
          },
        },
      );
    }
  }, [
    toWorkflowDef,
    workflowId,
    createWorkflow,
    updateWorkflow,
    resetDirty,
    navigate,
  ]);

  // Listen for keyboard shortcut save trigger
  useEffect(() => {
    const handler = () => handleSave();
    window.addEventListener("workflow:save", handler);
    return () => window.removeEventListener("workflow:save", handler);
  }, [handleSave]);

  const handleAutoLayout = useCallback(async () => {
    const def = toWorkflowDef();
    const tasks: WorkflowTaskDefSchema[] = def.tasks ?? [];
    if (tasks.length === 0) return;

    const result = await layoutWorkflow(tasks);
    setNodes(result.nodes);
    setEdges(result.edges);
    // fitView after layout settles
    requestAnimationFrame(() => fitView({ padding: 0.2, duration: 300 }));
  }, [toWorkflowDef, setNodes, setEdges, fitView]);

  const handleBack = useCallback(() => {
    if (workflowId) {
      navigate(`/workflows/${workflowId}`);
    } else {
      navigate("/workflows");
    }
  }, [workflowId, navigate]);

  return (
    <div className="flex h-12 items-center gap-1 border-b border-border bg-surface-sunken px-3">
      {/* Back */}
      <Button
        variant="ghost"
        size="icon-sm"
        onClick={handleBack}
        aria-label="Back"
      >
        <ArrowLeft />
      </Button>

      {/* Workflow name — inline editable */}
      <input
        ref={nameInputRef}
        value={workflowName}
        onChange={(e) => setName(e.target.value)}
        className="mx-2 h-7 max-w-[200px] truncate rounded border border-transparent bg-transparent px-1.5 font-display text-sm text-text outline-none transition-colors hover:border-border focus:border-ring focus:ring-1 focus:ring-ring/50"
        aria-label="Workflow name"
      />

      {/* Save indicator dot */}
      {isDirty && (
        <span
          className="size-1.5 rounded-full bg-primary"
          title="Unsaved changes"
        />
      )}

      <div className="mx-2 h-5 w-px bg-border" />

      {/* Save */}
      <Button
        variant="ghost"
        size="sm"
        onClick={handleSave}
        disabled={isSaving || (!isDirty && workflowId !== null)}
        className="gap-1.5"
      >
        <Save className="size-3.5" />
        <span className="text-xs">{isSaving ? "Saving..." : "Save"}</span>
      </Button>

      {/* Preview */}
      <Button
        variant="ghost"
        size="sm"
        onClick={() => {
          // PreviewPanel listens for this event
          window.dispatchEvent(new CustomEvent("workflow:preview"));
        }}
        disabled={!hasNodes}
        className="gap-1.5"
      >
        <Eye className="size-3.5" />
        <span className="text-xs">Preview</span>
      </Button>

      {/* Run — only for saved workflows */}
      {workflowId !== null && (
        <Button
          variant="ghost"
          size="sm"
          onClick={() => {
            window.dispatchEvent(new CustomEvent("workflow:run"));
          }}
          className="gap-1.5"
        >
          <Play className="size-3.5" />
          <span className="text-xs">Run</span>
        </Button>
      )}

      {/* Version History — only for saved workflows */}
      {workflowId !== null && (
        <Dialog>
          <DialogTrigger asChild>
            <Button
              variant="ghost"
              size="sm"
              className="gap-1.5"
              title="Version history"
            >
              <Clock className="size-3.5" />
              <span className="text-xs">History</span>
            </Button>
          </DialogTrigger>
          <DialogContent className="max-w-md">
            <DialogHeader>
              <DialogTitle>Version History</DialogTitle>
            </DialogHeader>
            <div className="max-h-96 overflow-y-auto">
              <VersionHistory workflowId={workflowId} />
            </div>
          </DialogContent>
        </Dialog>
      )}

      <div className="flex-1" />

      {/* Undo / Redo */}
      <Button
        variant="ghost"
        size="icon-sm"
        onClick={undo}
        disabled={!canUndo}
        aria-label="Undo"
        title="Undo (Ctrl+Z)"
      >
        <Undo2 className="size-3.5" />
      </Button>
      <Button
        variant="ghost"
        size="icon-sm"
        onClick={redo}
        disabled={!canRedo}
        aria-label="Redo"
        title="Redo (Ctrl+Shift+Z)"
      >
        <Redo2 className="size-3.5" />
      </Button>

      <div className="mx-1 h-5 w-px bg-border" />

      {/* Auto Layout */}
      <Button
        variant="ghost"
        size="icon-sm"
        onClick={handleAutoLayout}
        aria-label="Auto layout"
        title="Auto layout"
      >
        <LayoutGrid className="size-3.5" />
      </Button>

      {/* Zoom to Fit */}
      <Button
        variant="ghost"
        size="icon-sm"
        onClick={() => fitView({ padding: 0.2, duration: 300 })}
        aria-label="Zoom to fit"
        title="Zoom to fit"
      >
        <Maximize2 className="size-3.5" />
      </Button>

      {/* Delete selected */}
      <Button
        variant="ghost"
        size="icon-sm"
        onClick={removeSelected}
        aria-label="Delete selected"
        title="Delete selected"
      >
        <Trash2 className="size-3.5" />
      </Button>
    </div>
  );
}
