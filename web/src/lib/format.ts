/**
 * Shared date/time formatting utilities.
 *
 * Uses the browser's locale (undefined) for consistent behaviour across
 * the app rather than hardcoding "en-US" in some places and undefined
 * in others.
 */

/** Format a date string as "Jan 1, 2025". Returns `fallback` for nullish input. */
export function formatDate(
  dateStr: string | null | undefined,
  fallback = "\u2014",
): string {
  if (!dateStr) return fallback;
  return new Date(dateStr).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

/** Format a date string as "Jan 1, 2025, 02:30 PM". Returns `fallback` for nullish input. */
export function formatDateTime(
  dateStr: string | null | undefined,
  fallback = "Never",
): string {
  if (!dateStr) return fallback;
  return new Date(dateStr).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
