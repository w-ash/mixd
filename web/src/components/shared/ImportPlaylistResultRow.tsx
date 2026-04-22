import { Loader2 } from "lucide-react";
import type { SubOperationRecord } from "#/hooks/useOperationProgress";
import { pluralize } from "#/lib/pluralize";
import { cn } from "#/lib/utils";

import { StatusIndicator } from "./StatusIndicator";

interface ImportPlaylistResultRowProps {
  /** Accumulated outcome for this playlist. When ``null``, the row is
   * still running (pre-terminal sub-op state) and renders a placeholder. */
  record: SubOperationRecord | null;
  /** Display name fallback when the sub-op event hasn't carried one yet. */
  fallbackName: string;
  /** True while the operation is still in flight. Controls whether a
   * pending row shows the spinner. */
  isActive: boolean;
  className?: string;
}

/**
 * One row in the post-import results panel (or the in-flight per-playlist
 * tick list). Reuses ``StatusIndicator`` so the icon + color + label
 * vocabulary matches the rest of the app; falls back to a spinner-style
 * pending indicator when the sub-op hasn't emitted a terminal outcome.
 */
export function ImportPlaylistResultRow({
  record,
  fallbackName,
  isActive,
  className,
}: ImportPlaylistResultRowProps) {
  const name = record?.playlistName ?? fallbackName;
  const outcome = record?.outcome ?? null;

  if (outcome === null) {
    return (
      <div
        className={cn("flex items-center gap-2 px-4 py-3 text-sm", className)}
      >
        {isActive ? (
          <Loader2
            className="size-3 shrink-0 animate-spin text-text-muted"
            aria-hidden="true"
          />
        ) : (
          <span
            className="size-3 shrink-0 rounded-full bg-text-faint/30"
            aria-hidden="true"
          />
        )}
        <span className="truncate text-text">{name}</span>
        <span className="ml-auto shrink-0 text-xs text-text-muted">
          {isActive ? "pending…" : "not run"}
        </span>
      </div>
    );
  }

  if (outcome === "skipped_unchanged") {
    return (
      <div
        className={cn("flex items-center gap-2 px-4 py-3 text-sm", className)}
      >
        <StatusIndicator variant="info" label={name} />
        <span className="ml-auto shrink-0 text-xs text-text-muted">
          already up to date
        </span>
      </div>
    );
  }

  if (outcome === "failed") {
    const reason = record?.errorMessage ?? "Unknown error";
    const phase = record?.phase;
    const prefix = phase === "fetch" ? "failed while fetching" : "failed";
    return (
      <div
        className={cn("flex items-center gap-2 px-4 py-3 text-sm", className)}
      >
        <StatusIndicator variant="error" label={name} />
        <span
          className="ml-auto shrink-0 truncate text-xs text-text-muted"
          title={reason}
        >
          {prefix}: {reason}
        </span>
      </div>
    );
  }

  // succeeded
  const resolved = record?.resolved ?? 0;
  const unresolved = record?.unresolved ?? 0;
  const detail =
    unresolved > 0
      ? `${pluralize(resolved, "track")} resolved · ${unresolved} unresolved`
      : `${pluralize(resolved, "track")} resolved`;
  return (
    <div className={cn("flex items-center gap-2 px-4 py-3 text-sm", className)}>
      <StatusIndicator variant="success" label={name} />
      <span className="ml-auto shrink-0 text-xs text-text-muted font-mono">
        {detail}
      </span>
    </div>
  );
}
