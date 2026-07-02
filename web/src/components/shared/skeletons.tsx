import { Skeleton } from "#/components/ui/skeleton";
import { cn } from "#/lib/utils";

/**
 * Shared shimmer primitives replacing the per-page `*Skeleton` components.
 *
 * Bar/block dimensions are passed as Tailwind classes so each page keeps its
 * exact silhouette; every element renders the ui `<Skeleton>` (shimmer +
 * `data-slot="skeleton"`, which loading-state tests key off).
 */

interface ListRowsSkeletonProps {
  rows: number;
  /** Per-bar classes, e.g. "h-5 w-48"; "ml-auto …" reproduces a flex spacer. */
  bars: string[];
  /** "card" wraps each row in the bordered list-item chrome. */
  variant?: "plain" | "card";
}

export function ListRowsSkeleton({
  rows,
  bars,
  variant = "plain",
}: ListRowsSkeletonProps) {
  return (
    <div className="space-y-3">
      {Array.from({ length: rows }).map((_, row) => (
        <div
          // biome-ignore lint/suspicious/noArrayIndexKey: static skeleton placeholders
          key={row}
          className={cn(
            "flex items-center gap-4",
            variant === "card" &&
              "rounded-lg border border-border bg-surface-elevated px-4 py-3",
          )}
        >
          {bars.map((bar, i) => (
            <Skeleton
              // biome-ignore lint/suspicious/noArrayIndexKey: static skeleton placeholders
              key={i}
              className={bar}
            />
          ))}
        </div>
      ))}
    </div>
  );
}

/** Stacked full-width shimmer blocks (list-row or panel placeholders). */
export function BlocksSkeleton({
  count,
  className,
}: {
  count: number;
  className: string;
}) {
  return (
    <div className="space-y-3">
      {Array.from({ length: count }).map((_, i) => (
        <Skeleton
          // biome-ignore lint/suspicious/noArrayIndexKey: static skeleton placeholders
          key={i}
          className={className}
        />
      ))}
    </div>
  );
}

interface CardGridSkeletonProps {
  count: number;
  /** Column classes, e.g. "sm:grid-cols-2 lg:grid-cols-4". */
  gridClassName: string;
  /** Bars inside each card's chrome; omitted → each cell is a solid block. */
  bars?: string[];
}

export function CardGridSkeleton({
  count,
  gridClassName,
  bars,
}: CardGridSkeletonProps) {
  return (
    <div className={cn("grid gap-4", gridClassName)}>
      {Array.from({ length: count }).map((_, cell) =>
        bars ? (
          <div
            // biome-ignore lint/suspicious/noArrayIndexKey: static skeleton placeholders
            key={cell}
            className="rounded-xl border border-border-muted bg-surface p-5 space-y-3"
          >
            {bars.map((bar, i) => (
              <Skeleton
                // biome-ignore lint/suspicious/noArrayIndexKey: static skeleton placeholders
                key={i}
                className={bar}
              />
            ))}
          </div>
        ) : (
          <Skeleton
            // biome-ignore lint/suspicious/noArrayIndexKey: static skeleton placeholders
            key={cell}
            className="h-24 w-full"
          />
        ),
      )}
    </div>
  );
}

/** The detail-page header stanza: title bar + subtitle bar. */
export function DetailHeaderSkeleton({
  subtitleWidth = "w-96",
}: {
  subtitleWidth?: string;
}) {
  return (
    <div className="space-y-2">
      <Skeleton className="h-8 w-64" />
      <Skeleton className={cn("h-4", subtitleWidth)} />
    </div>
  );
}
