import { ReactFlowProvider } from "@xyflow/react";
import { LayoutPanelLeft, Monitor } from "lucide-react";
import { useEffect } from "react";
import { Link, useLocation, useParams } from "react-router";

import { useGetWorkflowApiV1WorkflowsWorkflowIdGet } from "#/api/generated/workflows/workflows";
import { PageHeader } from "#/components/layout/PageHeader";
import { Button } from "#/components/ui/button";
import { EditorCanvas } from "#/components/workflow/EditorCanvas";
import { EditorToolbar } from "#/components/workflow/EditorToolbar";
import { NodeConfigPanel } from "#/components/workflow/NodeConfigPanel";
import { NodePalette } from "#/components/workflow/NodePalette";
import { PreviewPanel } from "#/components/workflow/PreviewPanel";
import { useIsMobile } from "#/hooks/useIsMobile";
import { useEditorStore } from "#/stores/editor-store";

export default function WorkflowEditor() {
  const { id } = useParams<{ id: string }>();
  const workflowId = id ?? null;
  const location = useLocation();
  const isMobile = useIsMobile();
  const loadWorkflow = useEditorStore((s) => s.loadWorkflow);
  const isDirty = useEditorStore((s) => s.isDirty);

  // Fetch the workflow when editing an existing one; `/workflows/new` starts
  // empty. Templates are instantiated server-side via the gallery before the
  // editor opens, so there is no clone-on-load path here.
  const { data: workflowData } = useGetWorkflowApiV1WorkflowsWorkflowIdGet(
    workflowId ?? "",
    { query: { enabled: workflowId !== null } },
  );

  // Load workflow into editor store
  const workflow = workflowData?.status === 200 ? workflowData.data : undefined;

  useEffect(() => {
    if (workflow) {
      loadWorkflow(workflow.definition, workflow.id);
    }
  }, [workflow, loadWorkflow]);

  // A blank "New Workflow" must start clean. The singleton editor store survives
  // navigation, so without this a prior edit/import would leak into a fresh
  // /workflows/new. Skip the reset when an import just seeded the store — it
  // navigates here with `{ imported: true }`.
  useEffect(() => {
    const seededByImport = (location.state as { imported?: boolean } | null)
      ?.imported;
    if (workflowId === null && !seededByImport) {
      useEditorStore.getState().resetWorkflow();
    }
  }, [workflowId, location]);

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

  // Below the desktop breakpoint the React Flow canvas is unusable; render a
  // placeholder pointing the user at the runs view instead. The gate runs
  // after all hooks so the order stays stable across viewport changes.
  if (isMobile) {
    return (
      <>
        <title>Workflow editor — Mixd</title>
        <PageHeader
          title="Workflow editor"
          description="Editing a workflow's graph requires a larger screen."
        />
        <div className="rounded-xl border border-border bg-surface-elevated p-8 shadow-elevated">
          <div className="mx-auto flex max-w-md flex-col items-center text-center">
            <span
              aria-hidden="true"
              className="mb-4 flex size-12 items-center justify-center rounded-full bg-primary/10 text-primary"
            >
              <Monitor className="size-6" />
            </span>
            <h2 className="font-display text-base font-semibold text-text">
              Workflow editing needs a larger screen
            </h2>
            <p className="mt-2 text-sm text-text-muted">
              The graph canvas is desktop-only — open mixd on a screen at least
              1024px wide to edit the pipeline. Run history and other workflow
              actions stay fully usable here.
            </p>
            <div className="mt-6 flex flex-col gap-2 sm:flex-row">
              {workflowId && (
                <Button asChild>
                  <Link to={`/workflows/${workflowId}`}>
                    <LayoutPanelLeft className="mr-1.5 size-3.5" />
                    View runs
                  </Link>
                </Button>
              )}
              <Button variant="outline" asChild>
                <Link to="/workflows">All workflows</Link>
              </Button>
            </div>
          </div>
        </div>
      </>
    );
  }

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
