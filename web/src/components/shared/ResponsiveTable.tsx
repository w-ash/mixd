import type { ReactNode } from "react";

import { cn } from "#/lib/utils";

interface ResponsiveTableProps {
  /**
   * Tabular layout shown when the *content area* is wide enough (≥ 672px).
   * Typically a `<Table>` from `#/components/ui/table` with full columns.
   */
  table: ReactNode;
  /**
   * Card-list layout shown when the content area is narrow.
   * Typically `data.map(item => <Card />)` with the row's primary fields
   * + primary action.
   */
  cards: ReactNode;
  className?: string;
}

/**
 * Container-query swap between table and card-list views. The breakpoint
 * is the *content-area* width, not the viewport — iPad portrait at 820px
 * with a sidebar still has a narrow content area and falls back to cards.
 *
 * Threshold is `@2xl` (672px) — below that, columns become unreadable.
 * Both slots are always in the DOM; CSS handles visibility, so screen
 * readers see both. For SR-only consumers, prefer the table render.
 */
export function ResponsiveTable({
  table,
  cards,
  className,
}: ResponsiveTableProps) {
  return (
    <div className={cn("@container/table", className)}>
      <div className="@2xl/table:hidden">{cards}</div>
      <div className="hidden @2xl/table:block">{table}</div>
    </div>
  );
}
