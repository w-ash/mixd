import { formatCount } from "#/lib/format";
import { pluralSuffix } from "#/lib/pluralize";

interface ResultCountProps {
  total: number;
  /** Singular entity name, e.g. "track". */
  noun: string;
}

/**
 * Screen-reader announcement of the result count as filters are applied or
 * cleared (WCAG 2.2 status messages).
 */
export function ResultCount({ total, noun }: ResultCountProps) {
  return (
    <span className="sr-only" aria-live="polite">
      {`${total === 0 ? "No" : formatCount(total)} ${noun}${pluralSuffix(total)} match${total === 1 ? "es" : ""} current filters.`}
    </span>
  );
}
