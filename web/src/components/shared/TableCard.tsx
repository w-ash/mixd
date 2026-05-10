import type { ReactNode } from "react";

/**
 * Visual shell shared by every page-level Card that ResponsiveTable swaps in
 * below the @2xl/table container threshold (TrackCard, PlaylistTrackCard,
 * WorkflowCard, OutputTrackCard, etc.).
 *
 * Centralizes the radius / border / background / padding so a design tweak
 * hits one file. Body shapes vary too widely to genericize; callers compose
 * their own title row, metadata, and actions inside `children`.
 */
export interface TableCardProps {
  /** Fixed-width element on the left (checkbox, position number, rank). */
  leading?: ReactNode;
  /** Fixed-width element on the right (row-action menu, status badge). */
  trailing?: ReactNode;
  children: ReactNode;
}

export function TableCard({ leading, trailing, children }: TableCardProps) {
  return (
    <article className="flex items-start gap-3 rounded-md border border-border bg-surface px-3 py-3">
      {leading}
      <div className="min-w-0 flex-1">{children}</div>
      {trailing && <div className="shrink-0">{trailing}</div>}
    </article>
  );
}
