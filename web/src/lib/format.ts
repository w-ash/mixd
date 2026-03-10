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

/** Decode HTML entities (e.g. `&#x27;` → `'`). Spotify returns HTML-encoded descriptions. */
const HTML_ENTITIES: Record<string, string> = {
  "&amp;": "&",
  "&lt;": "<",
  "&gt;": ">",
  "&quot;": '"',
  "&#39;": "'",
  "&#x27;": "'",
  "&#x2F;": "/",
};
const ENTITY_RE = /&(?:amp|lt|gt|quot|#39|#x27|#x2F);/g;

export function decodeHtmlEntities(text: string): string {
  return text.replace(ENTITY_RE, (match) => HTML_ENTITIES[match] ?? match);
}

/** Format a number with locale-aware thousand separators (e.g. 1,234). */
export function formatCount(n: number): string {
  return n.toLocaleString();
}

/** Convert a snake_case metric name to Title Case (e.g. "lastfm_user_playcount" → "Lastfm User Playcount"). */
export function formatMetricHeader(name: string): string {
  return name.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

/** Format a metric cell value — numbers get locale formatting, nullish → "—", rest as-is. */
export function formatMetricValue(value: unknown): string {
  if (value == null) return "\u2014";
  if (typeof value === "number") return formatCount(value);
  return String(value);
}

/** Format milliseconds as "m:ss". Returns "\u2014" for nullish input. */
export function formatDuration(ms: number | null | undefined): string {
  if (ms == null) return "\u2014";
  const totalSec = Math.round(ms / 1000);
  const min = Math.floor(totalSec / 60);
  const sec = totalSec % 60;
  return `${min}:${String(sec).padStart(2, "0")}`;
}

/** Format a date as a relative time string ("3m ago", "2h ago", "5d ago"). Falls back to formatDate for older dates. */
export function formatRelativeTime(
  dateStr: string | null | undefined,
  fallback = "never",
): string {
  if (!dateStr) return fallback;
  const date = new Date(dateStr);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60_000);
  if (diffMins < 1) return "just now";
  if (diffMins < 60) return `${diffMins}m ago`;
  const diffHours = Math.floor(diffMins / 60);
  if (diffHours < 24) return `${diffHours}h ago`;
  const diffDays = Math.floor(diffHours / 24);
  if (diffDays < 7) return `${diffDays}d ago`;
  return formatDate(dateStr);
}
