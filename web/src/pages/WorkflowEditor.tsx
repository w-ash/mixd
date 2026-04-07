import { ReactFlowProvider } from "@xyflow/react";
import { useEffect } from "react";
import { useParams, useSearchParams } from "react-router";

import { useGetWorkflowApiV1WorkflowsWorkflowIdGet } from "#/api/generated/workflows/workflows";
import { EditorCanvas } from "#/components/workflow/EditorCanvas";
import { EditorToolbar } from "#/components/workflow/EditorToolbar";
import { NodeConfigPanel } from "#/components/workflow/NodeConfigPanel";
import { NodePalette } from "#/components/workflow/NodePalette";
import { PreviewPanel } from "#/components/workflow/PreviewPanel";
import { useEditorStore } from "#/stores/editor-store";

export default function WorkflowEditor() {
  const { id } = useParams<{ id: string }>();
  const [searchParams] = useSearchParams();
  const workflowId = id ?? null;
  const templateSourceId = searchParams.get("from");
  const loadWorkflow = useEditorStore((s) => s.loadWorkflow);
  const setName = useEditorStore((s) => s.setName);
  const isDirty = useEditorStore((s) => s.isDirty);

  // Fetch workflow for edit mode OR template source for cloning
  const fetchId = workflowId ?? templateSourceId;
  const { data: workflowData } = useGetWorkflowApiV1WorkflowsWorkflowIdGet(
    fetchId ?? "",
    { query: { enabled: fetchId !== null } },
  );

  // Load workflow into editor store
  const workflow = workflowData?.status === 200 ? workflowData.data : undefined;

  useEffect(() => {
    if (workflow) {
      if (templateSourceId) {
        // Clone template: load definition without workflow ID (creates new on save)
        loadWorkflow(workflow.definition);
        setName(`${workflow.name} (copy)`);
      } else {
        loadWorkflow(workflow.definition, workflow.id);
      }
    }
  }, [workflow, loadWorkflow, setName, templateSourceId]);

  // Unsaved changes guard
  useEffect(() => {
    const handler = (e: BeforeUnloadEvent) => {
      if (isDirty) e.preventDefault();
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [isDirty]);

  // Keyboard shortcuts
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const mod = e.metaKey || e.ctrlKey;

      if (mod && e.key === "s") {
        e.preventDefault();
        window.dispatchEvent(new CustomEvent("workflow:save"));
      }
      if (mod && e.key === "z" && !e.shiftKey) {
        e.preventDefault();
        useEditorStore.getState().undo();
      }
      if (mod && e.key === "z" && e.shiftKey) {
        e.preventDefault();
        useEditorStore.getState().redo();
      }
      if (mod && e.shiftKey && e.key === "p") {
        e.preventDefault();
        window.dispatchEvent(new CustomEvent("workflow:preview"));
      }
    };

    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  return (
    <ReactFlowProvider>
      <div className="flex h-[calc(100vh-3.5rem)] flex-col">
        <EditorToolbar />
        <div className="flex flex-1 overflow-hidden">
          <NodePalette />
          <div className="flex-1">
            <EditorCanvas />
          </div>
          <NodeConfigPanel />
        </div>
        <PreviewPanel />
      </div>
    </ReactFlowProvider>
  );
}
