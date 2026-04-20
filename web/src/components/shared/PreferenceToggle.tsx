import { CircleCheck, CircleHelp, CircleX, Star, X } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "#/lib/utils";

export type PreferenceState = "hmm" | "nah" | "yah" | "star";
type NullablePreferenceState = PreferenceState | null;

const STATES: {
  value: NullablePreferenceState;
  icon: ReactNode;
  label: string;
  activeClass: string;
}[] = [
  {
    value: "hmm",
    icon: <CircleHelp className="size-3.5" />,
    label: "Hmm — need to listen",
    activeClass: "text-amber-400 bg-amber-400/15",
  },
  {
    value: "nah",
    icon: <CircleX className="size-3.5" />,
    label: "Nah — not for me",
    activeClass: "text-text-muted bg-text-muted/15",
  },
  {
    value: "yah",
    icon: <CircleCheck className="size-3.5" />,
    label: "Yah — keep in rotation",
    activeClass: "text-emerald-400 bg-emerald-400/15",
  },
  {
    value: "star",
    icon: <Star className="size-3.5" />,
    label: "Star — always welcome",
    activeClass: "text-primary bg-primary/15",
  },
];

interface PreferenceToggleProps {
  value: NullablePreferenceState;
  onChange: (state: NullablePreferenceState) => void;
  disabled?: boolean;
  size?: "sm" | "default";
}

export function PreferenceToggle({
  value,
  onChange,
  disabled = false,
  size = "default",
}: PreferenceToggleProps) {
  return (
    <div
      className={cn(
        "inline-flex items-center rounded-lg border border-border-muted bg-surface-sunken",
        size === "sm" ? "gap-0.5 p-0.5" : "gap-1 p-1",
      )}
    >
      {STATES.map((state) => {
        const isActive = value === state.value;
        return (
          <button
            key={state.value}
            type="button"
            aria-pressed={isActive}
            aria-label={state.label}
            title={state.label}
            disabled={disabled}
            onClick={() => onChange(isActive ? null : state.value)}
            className={cn(
              "inline-flex items-center justify-center rounded-md transition-all",
              "hover:bg-accent/50 focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none",
              "disabled:pointer-events-none disabled:opacity-50",
              size === "sm" ? "size-6" : "size-7",
              isActive ? state.activeClass : "text-text-faint",
            )}
          >
            {state.icon}
          </button>
        );
      })}
      {value !== null && (
        <button
          type="button"
          aria-label="Clear preference"
          title="Clear preference"
          disabled={disabled}
          onClick={() => onChange(null)}
          className={cn(
            "inline-flex items-center justify-center rounded-md text-text-faint transition-all",
            "hover:bg-destructive/20 hover:text-destructive focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none",
            "disabled:pointer-events-none disabled:opacity-50",
            size === "sm" ? "size-6" : "size-7",
          )}
        >
          <X className="size-3" />
        </button>
      )}
    </div>
  );
}

/** Compact read-only preference badge for table cells */
export function PreferenceBadge({ state }: { state: PreferenceState }) {
  const config = STATES.find((s) => s.value === state);
  if (!config) return null;
  return (
    <span
      className={cn(
        "inline-flex items-center justify-center rounded-md size-5",
        config.activeClass,
      )}
      title={config.label}
    >
      {config.icon}
    </span>
  );
}
