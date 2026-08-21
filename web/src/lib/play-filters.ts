/**
 * Shared vocabulary for the play-count and play-recency filters.
 *
 * The Library filter panel (dropdown options) and the active-filter chips
 * (dismissible labels) both describe the same presets. They used to hardcode
 * the labels and day counts independently, which meant editing one without
 * the other silently desynced the chip from the dropdown that set it.
 */

/** A recency choice. Both bounds are always present so callers never narrow. */
export interface RecencyPreset {
  /** Stable select value. */
  value: string;
  label: string;
  playedWithin: number | null;
  notPlayedWithin: number | null;
}

/** Sentinel for "no recency filter" — the select's default option. */
export const RECENCY_ANY = "any";

export const RECENCY_PRESETS: readonly RecencyPreset[] = [
  {
    value: "within_7",
    label: "Played this week",
    playedWithin: 7,
    notPlayedWithin: null,
  },
  {
    value: "within_30",
    label: "Played this month",
    playedWithin: 30,
    notPlayedWithin: null,
  },
  {
    value: "not_within_730",
    label: "Not played in 2+ years",
    playedWithin: null,
    notPlayedWithin: 730,
  },
];

/** Preset play-count buckets (starting point, revisit against real data). */
export const PLAY_COUNT_PRESETS: readonly number[] = [10, 50];

/** The preset matching the current URL bounds, if any. */
export function findRecencyPreset(
  playedWithin: number | null,
  notPlayedWithin: number | null,
): RecencyPreset | undefined {
  return RECENCY_PRESETS.find(
    (preset) =>
      preset.playedWithin === playedWithin &&
      preset.notPlayedWithin === notPlayedWithin,
  );
}

/**
 * Chip label for a recency bound. Preset days reuse the dropdown's own
 * wording; anything else (a hand-edited URL) falls back to a plain day count.
 */
export function recencyLabel(days: string, negated: boolean): string {
  const n = Number(days);
  const preset = negated
    ? findRecencyPreset(null, n)
    : findRecencyPreset(n, null);
  if (preset) return preset.label;
  return negated ? `Not played in ${n} days` : `Played in last ${n} days`;
}

/**
 * How many filter groups the play filters contribute. Play count and recency
 * are one group each however they are expressed — "10+ plays" and "never
 * played" are two settings of the same control, not two active filters.
 */
export function countPlayFilters({
  minPlays,
  neverPlayed,
  playedWithin,
  notPlayedWithin,
}: {
  minPlays: string | null;
  neverPlayed: boolean;
  playedWithin: string | null;
  notPlayedWithin: string | null;
}): number {
  return (
    (minPlays || neverPlayed ? 1 : 0) +
    (playedWithin || notPlayedWithin ? 1 : 0)
  );
}
