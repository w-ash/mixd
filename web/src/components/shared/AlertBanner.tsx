/**
 * Warning banner primitive — left-accent bar, AlertTriangle, title + optional
 * detail. The single banner look shared by the per-schedule failure notice and
 * the dashboard aggregate. `role="alert"` for assistive tech; detail renders in
 * mono (timestamps/IDs) and truncates to one line by default. Pass
 * `truncateDetail={false}` when the detail is multi-item content (e.g. a list of
 * links) that must wrap instead of being clipped to the first line.
 */

import { AlertTriangle } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "#/lib/utils";

interface AlertBannerProps {
  title: ReactNode;
  detail?: ReactNode;
  /** Clip detail to one line with an ellipsis (default true). */
  truncateDetail?: boolean;
  className?: string;
}

export function AlertBanner({
  title,
  detail,
  truncateDetail = true,
  className,
}: AlertBannerProps) {
  return (
    <div
      role="alert"
      className={cn(
        "flex items-start gap-2.5 rounded-md border-l-2 border-secondary bg-secondary/10 px-4 py-3",
        className,
      )}
    >
      <AlertTriangle className="mt-0.5 size-4 shrink-0 text-secondary" />
      <div className="min-w-0">
        <p className="font-display text-sm text-text">{title}</p>
        {detail && (
          <p
            className={cn(
              "mt-0.5 font-mono text-xs text-text-muted",
              truncateDetail && "truncate",
            )}
          >
            {detail}
          </p>
        )}
      </div>
    </div>
  );
}
