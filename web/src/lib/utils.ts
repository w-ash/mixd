import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** Right-aligned numeric cell treatment (counts, durations) shared by the workflow run tables and rows. */
export const numericCellClass =
  "text-right font-mono text-xs tabular-nums text-text-muted";
