import { Input } from "#/components/ui/input";
import { formatCount } from "#/lib/format";
import { pluralSuffix } from "#/lib/pluralize";

interface ListSearchInputProps {
  /** Text shown in the box (the undebounced value). */
  value: string;
  /** Called with the typed text, or `null` when Escape clears the box. */
  onChange: (next: string | null) => void;
  /** True while the typed text has not yet reached the query. */
  isSearching: boolean;
  placeholder: string;
  ariaLabel: string;
}

/** Search box for list pages: Escape clears, and a "Searching..." cue shows while pending. */
export function ListSearchInput({
  value,
  onChange,
  isSearching,
  placeholder,
  ariaLabel,
}: ListSearchInputProps) {
  return (
    <div className="relative flex-1 min-w-48">
      <Input
        type="search"
        placeholder={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Escape" && value !== "") {
            e.preventDefault();
            onChange(null);
          }
        }}
        aria-label={ariaLabel}
        className={isSearching ? "opacity-70" : ""}
      />
      {isSearching && (
        <span
          className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-text-muted"
          aria-live="polite"
        >
          Searching...
        </span>
      )}
    </div>
  );
}

interface ResultCountProps {
  total: number;
  /** Singular entity name, e.g. "track". */
  noun: string;
}

/** Screen-reader announcement of the result count (WCAG 2.2 status messages). */
export function ResultCount({ total, noun }: ResultCountProps) {
  return (
    <span className="sr-only" aria-live="polite">
      {total > 0
        ? `${formatCount(total)} ${noun}${pluralSuffix(total)} match${total === 1 ? "es" : ""} current filters.`
        : `No ${noun}s match current filters.`}
    </span>
  );
}
