// Per-request reasoning effort for the workflow assistant. The user picks per
// task — quick lookups don't need deep reasoning, "build me a playlist that…"
// does. Mirrors the EffortLevel subset the backend will expose on the chat
// request (wired in a later phase; dormant in Phase 0).

export type EffortChoice = "quick" | "standard" | "thorough";

const STORAGE_KEY = "mixd:chatEffort";

/** UI choice → backend effort value. Consumed by the Phase 1 SSE client. */
export const EFFORT_API_VALUES: Record<EffortChoice, string> = {
  quick: "low",
  standard: "high",
  thorough: "xhigh",
};

export const EFFORT_OPTIONS: Array<{ value: EffortChoice; label: string }> = [
  { value: "quick", label: "Quick" },
  { value: "standard", label: "Standard" },
  { value: "thorough", label: "Thorough" },
];

export function getStoredEffort(): EffortChoice {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === "quick" || stored === "standard" || stored === "thorough") {
      return stored;
    }
  } catch {
    // Private browsing or storage unavailable — fall through to the default.
  }
  return "standard";
}

export function storeEffort(choice: EffortChoice): void {
  try {
    localStorage.setItem(STORAGE_KEY, choice);
  } catch {
    // Silently ignore — the choice just won't persist across reloads.
  }
}
