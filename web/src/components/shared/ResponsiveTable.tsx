import { type ReactNode, useRef } from "react";

import { useContainerQuery } from "#/hooks/useContainerQuery";
import { cn } from "#/lib/utils";

/** Below this content width, table columns become unreadable (Tailwind `@2xl`). */
const TABLE_MIN_WIDTH_PX = 672;

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
 * Renders a row collection as a table or a card list, whichever fits the
 * *content-area* width — not the viewport, so iPad portrait at 820px with a
 * sidebar still gets cards.
 *
 * Exactly one branch is mounted, so rows are built once and assistive
 * technology sees a single copy of the data. The wrapper measures itself in a
 * layout effect, before the browser paints; until then the branch follows the
 * document width, so a desktop viewer mounts the table without the cards
 * running their effects first.
 */
export function ResponsiveTable({
  table,
  cards,
  className,
}: ResponsiveTableProps) {
  const wrapperRef = useRef<HTMLDivElement>(null);
  const isWide = useContainerQuery(wrapperRef, TABLE_MIN_WIDTH_PX);

  return (
    <div ref={wrapperRef} className={cn("@container/table", className)}>
      {isWide ? table : cards}
    </div>
  );
}
