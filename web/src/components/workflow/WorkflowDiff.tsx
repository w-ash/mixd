/**
 * Side-by-side DAG diff view comparing a historical version against the current workflow.
 *
 * Renders two WorkflowGraph instances with diff highlighting:
 * - Green ring → added nodes (only in new)
 * - Red ring → removed nodes (only in old)
 * - Yellow ring → modified nodes (config or connections changed)
 */

import { useMemo } from "react";
import type {
  WorkflowTaskDefSchema,
  WorkflowVersionSchema,
} from "#/api/generated/model";
import { useGetWorkflowVersionApiV1WorkflowsWorkflowIdVersionsVersionGet } from "#/api/generated/workflows/workflows";
import { WorkflowGraph } from "#/components/shared/WorkflowGraph";
import { diffWorkflowDefs } from "#/lib/workflow-diff";
import { useEditorStore } from "#/stores/editor-store";

interface WorkflowDiffProps {
  workflowId: string;
  version: number;
}

export function WorkflowDiff({ workflowId, version }: WorkflowDiffProps) {
  const nodes = useEditorStore((s) => s.nodes);
  const edges = useEditorStore((s) => s.edges);
  const currentTasks = useMemo<WorkflowTaskDefSchema[]>(
    () =>
      nodes.map((node) => ({
        id: node.data.taskId as string,
        type: node.data.nodeType as string,
        config: (node.data.config as Record<string, unknown>) ?? {},
        upstream: edges
          .filter((e) => e.target === node.id)
          .map((e) => e.source),
      })),
    [nodes, edges],
  );

  const { data: versionData } =
    useGetWorkflowVersionApiV1WorkflowsWorkflowIdVersionsVersionGet(
      workflowId,
      version,
    );

  const versionDef =
    versionData?.status === 200
      ? (versionData.data as WorkflowVersionSchema).definition
      : null;

  if (!versionDef) {
    return (
      <div className="flex h-64 items-center justify-center">
        <span className="font-display text-sm text-text-muted">
          Loading version...
        </span>
      </div>
    );
  }

  const oldTasks = versionDef.tasks ?? [];
  const diff = diffWorkflowDefs(oldTasks, currentTasks);

  // Build highlight maps for each side
  const oldIds = new Set(oldTasks.map((t) => t.id));
  const newIds = new Set(currentTasks.map((t) => t.id));
  const oldHighlightMap = new Map(
    [...diff.highlightMap.entries()].filter(([id]) => oldIds.has(id)),
  );
  const newHighlightMap = new Map(
    [...diff.highlightMap.entries()].filter(([id]) => newIds.has(id)),
  );

  return (
    <div className="space-y-3">
      {/* Diff legend */}
      <div className="flex gap-4 px-1">
        <span className="flex items-center gap-1.5 font-display text-[10px] text-text-muted">
          <span className="inline-block size-2.5 rounded-full bg-status-connected/60" />
          Added ({diff.added.length})
        </span>
        <span className="flex items-center gap-1.5 font-display text-[10px] text-text-muted">
          <span className="inline-block size-2.5 rounded-full bg-destructive/60" />
          Removed ({diff.removed.length})
        </span>
        <span className="flex items-center gap-1.5 font-display text-[10px] text-text-muted">
          <span className="inline-block size-2.5 rounded-full bg-[oklch(0.8_0.14_85)]/60" />
          Modified ({diff.modified.length})
        </span>
      </div>

      {/* Side-by-side graphs */}
      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <p className="font-display text-[10px] font-semibold uppercase tracking-wider text-text-faint">
            Version {version} (previous)
          </p>
          <div className="h-72 rounded border border-border">
            <WorkflowGraph tasks={oldTasks} highlightMap={oldHighlightMap} />
          </div>
        </div>
        <div className="space-y-1.5">
          <p className="font-display text-[10px] font-semibold uppercase tracking-wider text-text-faint">
            Current
          </p>
          <div className="h-72 rounded border border-border">
            <WorkflowGraph
              tasks={currentTasks}
              highlightMap={newHighlightMap}
            />
          </div>
        </div>
      </div>
    </div>
  );
}
