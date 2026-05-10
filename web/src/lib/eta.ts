/**
 * Pure threshold logic for displaying live progress with ETA.
 *
 * Backend ProgressCoordinator already calculates items_per_second and
 * eta_seconds and sends them on every sub_progress event, throttled to
 * 4 Hz so the rate is already smoothed by the time we see it. The
 * question here is *when* to show them: showing "ETA 1s" that's wildly
 * wrong is worse than showing nothing.
 *
 * Show ETA only when:
 *   - total is known and > 0
 *   - itemsPerSecond > 0 (otherwise the rate is meaningless)
 *   - completion < 80% (don't show "ETA 1s" right before done)
 *   - etaSeconds > 3 (sub-3s ETAs flicker — the work finishes before
 *     the user can read the number)
 *
 * Below threshold: "Enriching 12/87 tracks…"
 * Above threshold: "Enriching 12/87 · 12/sec · ETA 6s"
 */

export interface ProgressLabelInput {
  /** Current item count (from sub_progress event). */
  current: number;
  /** Total expected items. null/undefined = indeterminate. */
  total: number | null | undefined;
  /** Human-readable message from the sub-op (e.g., "Fetching lastfm metadata"). */
  message: string;
  /** items_per_second from the most recent event. */
  itemsPerSecond?: number | null;
  /** eta_seconds from the most recent event. */
  etaSeconds?: number | null;
}

const COMPLETION_LIMIT = 0.8;
const MIN_ETA_SECONDS = 3;

export interface FormatResult {
  /** True if the ETA portion is shown. */
  hasEta: boolean;
  /** Full label, suitable for direct rendering. */
  label: string;
}

/**
 * Compute the display label for a sub_progress update.
 *
 * Pure — easy to test in isolation. Takes the message verb (the
 * leading word of message, e.g., "Enriching" / "Fetching") plus
 * the rate/total/eta and formats a single status line.
 */
export function formatProgressLabel(input: ProgressLabelInput): FormatResult {
  const { current, total, message, itemsPerSecond, etaSeconds } = input;

  const verb = message.split(" ")[0] || "Processing";

  if (total == null || total <= 0) {
    return { hasEta: false, label: `${verb} ${current} items…` };
  }

  const baseLabel = `${verb} ${current}/${total} tracks`;
  const completion = current / total;

  const showEta =
    completion < COMPLETION_LIMIT &&
    typeof itemsPerSecond === "number" &&
    itemsPerSecond > 0 &&
    typeof etaSeconds === "number" &&
    etaSeconds > MIN_ETA_SECONDS;

  if (!showEta) {
    return { hasEta: false, label: `${baseLabel}…` };
  }

  const rate = itemsPerSecond as number;
  const eta = etaSeconds as number;
  const ratePart = `${rate.toFixed(rate < 10 ? 1 : 0)}/sec`;
  const etaPart = `ETA ${Math.round(eta)}s`;
  return { hasEta: true, label: `${baseLabel} · ${ratePart} · ${etaPart}` };
}
