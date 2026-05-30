import { LayoutTemplate } from "lucide-react";
import { type ReactNode, useState } from "react";
import { useNavigate } from "react-router";

import type { WorkflowTemplateSchema } from "#/api/generated/model";
import {
  useListWorkflowTemplatesApiV1WorkflowsTemplatesGet,
  useUseWorkflowTemplateApiV1WorkflowsTemplatesTemplateIdUsePost,
} from "#/api/generated/workflows/workflows";
import { EmptyState } from "#/components/shared/EmptyState";
import { NodeTypeBadge } from "#/components/shared/NodeTypeBadge";
import { QueryErrorState } from "#/components/shared/QueryErrorState";
import {
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "#/components/ui/dialog";
import { ResponsiveDialog } from "#/components/ui/responsive-dialog";
import { Skeleton } from "#/components/ui/skeleton";
import { toasts } from "#/lib/toasts";

/** One selectable template card in the gallery list. */
function TemplateCard({
  template,
  onUse,
  isPending,
}: {
  template: WorkflowTemplateSchema;
  onUse: (id: string) => void;
  isPending: boolean;
}) {
  // De-dupe the category badges — a template often has multiple nodes of the
  // same category and we only want one chip per category in the summary.
  const categories = [
    ...new Set(template.node_types.map((t) => t.split(".")[0])),
  ];

  return (
    <button
      type="button"
      disabled={isPending}
      onClick={() => onUse(template.id)}
      className="group flex w-full flex-col items-start gap-2 rounded-md border border-border bg-surface px-4 py-3 text-left transition-colors hover:border-primary/50 hover:bg-surface-elevated disabled:pointer-events-none disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
    >
      <div className="flex w-full items-baseline justify-between gap-2">
        <span className="font-display text-sm font-medium text-text group-hover:text-primary">
          {template.name}
        </span>
        <span className="shrink-0 tabular-nums text-xs text-text-muted">
          {template.task_count} task{template.task_count === 1 ? "" : "s"}
        </span>
      </div>
      {template.description && (
        <p className="line-clamp-2 text-xs text-text-muted">
          {template.description}
        </p>
      )}
      {categories.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {categories.map((c) => (
            <NodeTypeBadge key={c} nodeType={c} />
          ))}
        </div>
      )}
    </button>
  );
}

function GallerySkeleton() {
  return (
    <div className="flex flex-col gap-3">
      {Array.from({ length: 4 }).map((_, i) => (
        // biome-ignore lint/suspicious/noArrayIndexKey: static skeleton placeholders
        <Skeleton key={i} className="h-20 w-full rounded-md" />
      ))}
    </div>
  );
}

/**
 * "Start from a template" gallery. Lists the built-in, file-backed templates
 * from `GET /workflows/templates`; selecting one instantiates a new
 * user-owned, editable workflow server-side and opens it in the editor.
 */
export function TemplateGalleryDialog({ trigger }: { trigger: ReactNode }) {
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();

  const { data, isLoading, isError, error } =
    useListWorkflowTemplatesApiV1WorkflowsTemplatesGet({
      query: { enabled: open },
    });

  const useTemplate =
    useUseWorkflowTemplateApiV1WorkflowsTemplatesTemplateIdUsePost({
      mutation: {
        onSuccess: (res) => {
          if (res.status === 201) {
            setOpen(false);
            toasts.success("Workflow created from template");
            navigate(`/workflows/${res.data.id}/edit`);
          }
        },
        meta: { errorLabel: "Failed to create workflow from template" },
      },
    });

  const templates = data?.status === 200 ? data.data : [];

  return (
    <ResponsiveDialog
      open={open}
      onOpenChange={setOpen}
      trigger={trigger}
      className="max-w-lg"
    >
      <DialogHeader>
        <DialogTitle>Start from a template</DialogTitle>
        <DialogDescription>
          Pick a built-in pipeline. It's copied into a new workflow you own and
          can edit freely.
        </DialogDescription>
      </DialogHeader>

      <div className="mt-4 max-h-[60vh] overflow-y-auto">
        {isLoading && <GallerySkeleton />}

        {isError && (
          <QueryErrorState error={error} heading="Failed to load templates" />
        )}

        {!isLoading && !isError && templates.length === 0 && (
          <EmptyState
            icon={<LayoutTemplate className="size-10" />}
            heading="No templates available"
            description="There are no built-in templates to start from right now."
          />
        )}

        {!isLoading && !isError && templates.length > 0 && (
          <div className="flex flex-col gap-3">
            {templates.map((template) => (
              <TemplateCard
                key={template.id}
                template={template}
                isPending={useTemplate.isPending}
                onUse={(id) => useTemplate.mutate({ templateId: id })}
              />
            ))}
          </div>
        )}
      </div>
    </ResponsiveDialog>
  );
}
