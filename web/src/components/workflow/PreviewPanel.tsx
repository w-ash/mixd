/**
 * Bottom drawer showing workflow preview / dry-run results.
 *
 * Three states: closed (collapsed bar), loading (SSE progress), results (tracks + node summaries).
 * Listens for "workflow:preview" custom event from toolbar to trigger.
 */

import { ChevronDown, ChevronUp, Eye, Loader2, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  type PreviewResult,
  useWorkflowPreview,
} from "@/hooks/useWorkflowPreview";
import { getNodeCategory } from "@/lib/workflow-config";

function NodeSummaryList({ result }: { result: PreviewResult }) {
  if (result.node_summaries.length === 0) return null;

  return (
    <div className="space-y-1.5">
      <p className="font-display text-[10px] font-semibold uppercase tracking-wider text-text-muted">
        Pipeline
      </p>
      <div className="flex flex-wrap gap-2">
        {result.node_summaries.map((ns) => {
          const cat = getNodeCategory(ns.node_type);
          return (
            <div
              key={ns.node_id}
              className="flex items-center gap-1.5 rounded border-l-2 bg-surface-elevated px-2 py-1"
              style={{ borderLeftColor: cat.accentColor }}
            >
              <cat.Icon
                size={11}
                strokeWidth={1.5}
                style={{ color: cat.accentColor }}
                aria-hidden="true"
              />
              <span className="font-mono text-[10px] text-text-muted">
                {ns.node_id}
              </span>
              <span className="font-mono text-[10px] text-text-faint">
                {ns.track_count} tracks
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function OutputTracksTable({ result }: { result: PreviewResult }) {
  if (result.output_tracks.length === 0) {
    return (
      <p className="pt-2 font-body text-xs text-text-faint">
        No output tracks.
      </p>
    );
  }

  return (
    <div className="space-y-1.5">
      <p className="font-display text-[10px] font-semibold uppercase tracking-wider text-text-muted">
        Output ({result.output_tracks.length} tracks)
      </p>
      <div className="max-h-48 overflow-y-auto rounded border border-border">
        <table className="w-full text-left">
          <thead>
            <tr className="border-b border-border bg-surface-sunken">
              <th className="w-10 px-2 py-1 font-display text-[10px] font-semibold text-text-faint">
                #
              </th>
              <th className="px-2 py-1 font-display text-[10px] font-semibold text-text-faint">
                Title
              </th>
              <th className="px-2 py-1 font-display text-[10px] font-semibold text-text-faint">
                Artist
              </th>
            </tr>
          </thead>
          <tbody>
            {result.output_tracks.map((t) => (
              <tr
                key={t.rank}
                className="border-b border-border/50 last:border-0"
              >
                <td className="px-2 py-1 font-mono text-[10px] text-text-faint">
                  {t.rank}
                </td>
                <td className="max-w-[200px] truncate px-2 py-1 font-body text-xs text-text">
                  {t.title}
                </td>
                <td className="max-w-[150px] truncate px-2 py-1 font-body text-xs text-text-muted">
                  {t.artists}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function PreviewPanel() {
  const [isOpen, setIsOpen] = useState(false);
  const { isPreviewRunning, previewResult, error, startPreview, clearPreview } =
    useWorkflowPreview();

  // Listen for toolbar preview trigger
  useEffect(() => {
    const handler = () => {
      setIsOpen(true);
      startPreview();
    };
    window.addEventListener("workflow:preview", handler);
    return () => window.removeEventListener("workflow:preview", handler);
  }, [startPreview]);

  const handleClose = useCallback(() => {
    clearPreview();
    setIsOpen(false);
  }, [clearPreview]);

  // Collapsed bar
  if (!isOpen) {
    return (
      <button
        type="button"
        onClick={() => setIsOpen(true)}
        className="flex h-8 items-center gap-1.5 border-t border-border bg-surface-sunken px-4 text-text-faint transition-colors hover:text-text-muted"
      >
        <ChevronUp size={12} />
        <span className="font-display text-[10px] uppercase tracking-wider">
          Preview
        </span>
      </button>
    );
  }

  return (
    <div className="flex max-h-[40vh] flex-col border-t border-border bg-surface-sunken">
      {/* Header */}
      <div className="flex shrink-0 items-center justify-between border-b border-border px-4 py-2">
        <div className="flex items-center gap-2">
          <Eye size={13} className="text-primary" />
          <span className="font-display text-xs text-text">Preview</span>
          <span className="rounded bg-primary/10 px-1.5 py-0.5 font-mono text-[10px] text-primary">
            dry run
          </span>
        </div>
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="icon-xs"
            onClick={() => setIsOpen(false)}
            aria-label="Collapse"
          >
            <ChevronDown size={12} />
          </Button>
          <Button
            variant="ghost"
            size="icon-xs"
            onClick={handleClose}
            aria-label="Close preview"
          >
            <X size={12} />
          </Button>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-4">
        {/* Banner */}
        <div className="mb-3 rounded border border-primary/20 bg-primary/5 px-3 py-1.5">
          <p className="font-body text-[11px] text-primary/80">
            Preview mode — no playlists were modified
          </p>
        </div>

        {isPreviewRunning && (
          <div className="flex items-center gap-2 py-8 justify-center">
            <Loader2 size={16} className="animate-spin text-primary" />
            <span className="font-display text-xs text-text-muted">
              Running preview...
            </span>
          </div>
        )}

        {error && (
          <div className="rounded border border-destructive/30 bg-destructive/5 px-3 py-2">
            <p className="font-body text-xs text-destructive">
              {error.message}
            </p>
          </div>
        )}

        {previewResult && !isPreviewRunning && (
          <div className="space-y-4">
            <NodeSummaryList result={previewResult} />
            <OutputTracksTable result={previewResult} />
          </div>
        )}
      </div>
    </div>
  );
}
