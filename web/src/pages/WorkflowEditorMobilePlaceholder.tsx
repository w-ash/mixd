import { LayoutPanelLeft, Monitor } from "lucide-react";
import { Link } from "react-router";

import { PageHeader } from "#/components/layout/PageHeader";
import { Button } from "#/components/ui/button";

interface WorkflowEditorMobilePlaceholderProps {
  /** ID of the workflow being viewed; null for the "+ New" flow. */
  workflowId: string | null;
}

/**
 * Shown in place of the editor on viewports below `lg:` (1024px). The graph
 * canvas is unusable on phones and degrading the touch interactions isn't
 * worth the cost; instead we redirect users to the most useful nearby thing
 * — the workflow's run history — and explicitly state the constraint.
 */
export function WorkflowEditorMobilePlaceholder({
  workflowId,
}: WorkflowEditorMobilePlaceholderProps) {
  return (
    <div>
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
    </div>
  );
}
