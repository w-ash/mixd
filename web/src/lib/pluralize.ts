/**
 * pluralize(1, "track")          → "1 track"
 * pluralize(5, "track")          → "5 tracks"
 * pluralize(2, "entry", "entries") → "2 entries"
 *
 * pluralSuffix(1) → ""   pluralSuffix(3) → "s"
 */

export function pluralize(
  count: number,
  singular: string,
  plural: string = `${singular}s`,
): string {
  return `${count} ${count === 1 ? singular : plural}`;
}

export function pluralSuffix(count: number): "" | "s" {
  return count === 1 ? "" : "s";
}
