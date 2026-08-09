/**
 * Pure derivations over a Spotify GDPR import queue.
 *
 * The server sends entries and nothing else: every number here is one pass over
 * that list, and a server-side roll-up would be a second source of truth for
 * facts the client can compute exactly. Kept out of the view so the two
 * judgement calls are testable — what "progress" means for a batch, and when an
 * estimate is honest enough to show.
 */

import type { ImportQueueEntrySchema } from "#/api/generated/model";

/** Statuses an entry can still move on from. */
const LIVE_STATUSES = new Set(["queued", "running"]);

/** One sample says nothing about variance, and a confidently wrong "~2 min
 * left" discredits every other number on the panel. */
const MIN_SETTLED_FOR_ETA = 2;

export function isSettled(entry: ImportQueueEntrySchema): boolean {
  return !LIVE_STATUSES.has(entry.status);
}

export interface QueueSummary {
  total: number;
  settled: number;
  succeeded: number;
  /** Finished, but with items it could not process — worth a look, not alarm. */
  partial: number;
  failed: number;
  cancelled: number;
  queued: number;
  running: number;
  /** Whole-batch progress: settled files over total, 0–100. */
  percent: number;
  /** Plays imported so far, summed across settled files. */
  plays: number;
  /** Seconds until the queue drains, or null while it would be a guess. */
  etaSeconds: number | null;
}

function playsOf(entry: ImportQueueEntrySchema): number {
  const value = entry.counts?.track_plays;
  return typeof value === "number" ? value : 0;
}

function durationMs(entry: ImportQueueEntrySchema): number | null {
  if (!entry.started_at || !entry.settled_at) return null;
  const ms =
    new Date(entry.settled_at).getTime() - new Date(entry.started_at).getTime();
  return ms > 0 ? ms : null;
}

/**
 * Observed rate in bytes per millisecond, or null when not yet knowable.
 *
 * By bytes, not file count: a GDPR export's files differ several-fold, so a
 * per-file average lurches every time a small one goes through.
 */
function bytesPerMs(entries: ImportQueueEntrySchema[]): number | null {
  let bytes = 0;
  let ms = 0;
  let counted = 0;
  for (const entry of entries) {
    const elapsed = durationMs(entry);
    if (elapsed === null || entry.size_bytes === undefined) continue;
    bytes += entry.size_bytes;
    ms += elapsed;
    counted += 1;
  }
  if (counted < MIN_SETTLED_FOR_ETA || ms <= 0 || bytes <= 0) return null;
  return bytes / ms;
}

export function summarizeQueue(
  entries: ImportQueueEntrySchema[],
): QueueSummary {
  const tally = {
    succeeded: 0,
    partial: 0,
    failed: 0,
    cancelled: 0,
    queued: 0,
    running: 0,
  };
  let plays = 0;
  for (const entry of entries) {
    plays += playsOf(entry);
    switch (entry.status) {
      case "complete":
        tally.succeeded += 1;
        break;
      case "partial":
        tally.partial += 1;
        break;
      case "error":
        tally.failed += 1;
        break;
      case "cancelled":
        tally.cancelled += 1;
        break;
      case "running":
        tally.running += 1;
        break;
      default:
        tally.queued += 1;
    }
  }
  const settled = entries.filter(isSettled).length;
  const rate = bytesPerMs(entries);
  const remainingBytes = entries
    .filter((entry) => !isSettled(entry))
    .reduce((sum, entry) => sum + (entry.size_bytes ?? 0), 0);

  return {
    ...tally,
    total: entries.length,
    settled,
    // Finished files only, never blended with the running file's percentage —
    // blending is what slides a batch bar backwards at each handover.
    percent: entries.length === 0 ? 0 : (settled / entries.length) * 100,
    plays,
    etaSeconds:
      rate === null || remainingBytes === 0
        ? null
        : Math.round(remainingBytes / rate / 1000),
  };
}

/**
 * Rows in reading order: failures first, then upload order.
 *
 * A failure is the one thing here that may need a person, so it must not sink
 * down the list as the export goes on.
 */
export function orderedEntries(
  entries: ImportQueueEntrySchema[],
): ImportQueueEntrySchema[] {
  const rank = (entry: ImportQueueEntrySchema) =>
    entry.status === "error" ? 0 : 1;
  return [...entries].sort(
    (a, b) => rank(a) - rank(b) || a.position - b.position,
  );
}

export interface QueuedPlacement {
  /** 1-based place in the line, counting only files still waiting. */
  place: number;
  /** Seconds until this file is expected to start, or null when unknowable. */
  startsInSeconds: number | null;
}

/**
 * Where a waiting file sits, and roughly when its turn comes.
 *
 * A bare "Queued" chip looks identical at minute 0 and minute 25 of a serial
 * drain, which reads as stuck.
 */
export function placementOf(
  entries: ImportQueueEntrySchema[],
  entry: ImportQueueEntrySchema,
): QueuedPlacement | null {
  if (entry.status !== "queued") return null;
  const waiting = entries
    .filter((e) => e.status === "queued")
    .sort((a, b) => a.position - b.position);
  const place = waiting.findIndex((e) => e.position === entry.position) + 1;

  const rate = bytesPerMs(entries);
  if (rate === null) return { place, startsInSeconds: null };

  const bytesAhead = entries
    .filter(
      (e) =>
        (e.status === "running" || e.status === "queued") &&
        e.position < entry.position,
    )
    .reduce((sum, e) => sum + (e.size_bytes ?? 0), 0);
  return { place, startsInSeconds: Math.round(bytesAhead / rate / 1000) };
}
