import { Input } from "#/components/ui/input";

interface ListSearchInputProps {
  /** Text shown in the box (the undebounced value). */
  value: string;
  /** Called with the typed text; Escape calls it with `""`. */
  onChange: (next: string) => void;
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
            onChange("");
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
