/**
 * The two row flourishes the data tables share: an entrance cascade and the
 * gold bar that marks the hovered row.
 */

import type { CSSProperties } from "react";

/** Rows past this render immediately — a long page would crawl in otherwise. */
const STAGGER_CAP = 15;

/** Entrance animation for row `index`, or nothing past the cascade's cap. */
export function rowStaggerStyle(index: number): CSSProperties | undefined {
  return index < STAGGER_CAP
    ? { animation: `fade-in-row 300ms ease-out ${index * 20}ms both` }
    : undefined;
}

/**
 * Gold accent bar down the left edge of the hovered row. Absolutely
 * positioned: the row needs `group relative`, the cell `relative`.
 */
export function RowHoverAccent() {
  return (
    <span className="absolute left-0 top-1 bottom-1 w-0.5 rounded-full bg-primary opacity-0 group-hover:opacity-100 transition-opacity" />
  );
}
