import { ArrowUp } from "lucide-react";

import { TableHead } from "#/components/ui/table";
import type { SortDirection } from "#/hooks/sort-param";
import { cn } from "#/lib/utils";

/** Sort state a caller owns, handed to a table so its headers can drive it. */
export interface TableSort<F extends string> {
  field: F;
  dir: SortDirection;
  /** Display name per field, for the header's `aria-label`. */
  labels: Record<F, string>;
  onSort: (field: F, dir: SortDirection) => void;
}

interface SortableHeadProps<F extends string> {
  field: F;
  /** Omitted where the caller owns no sort state; the header goes plain. */
  sort: TableSort<F> | undefined;
  className?: string;
  children: React.ReactNode;
}

/**
 * Column header that toggles direction, or sets a new sort when it is not the
 * active one. The arrow marks the active column and points the direction the
 * rows are ordered in, never the direction a click would produce.
 */
export function SortableHead<F extends string>({
  field,
  sort,
  className,
  children,
}: SortableHeadProps<F>) {
  if (!sort) {
    return <TableHead className={className}>{children}</TableHead>;
  }

  const isActive = field === sort.field;
  const nextDir: SortDirection =
    isActive && sort.dir === "asc" ? "desc" : "asc";

  return (
    <TableHead
      className={className}
      aria-sort={
        isActive ? (sort.dir === "asc" ? "ascending" : "descending") : "none"
      }
    >
      <button
        type="button"
        className="inline-flex items-center gap-1 hover:text-text transition-colors"
        onClick={() => sort.onSort(field, nextDir)}
        aria-label={`Sort by ${sort.labels[field]} ${nextDir === "asc" ? "ascending" : "descending"}`}
      >
        {children}
        {isActive && (
          <ArrowUp
            className={cn(
              "size-3 transition-transform duration-150",
              sort.dir === "desc" && "rotate-180",
            )}
            aria-hidden="true"
          />
        )}
      </button>
    </TableHead>
  );
}
