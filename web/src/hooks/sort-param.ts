/**
 * One `?sort=` codec for every URL-backed filter hook.
 *
 * The param is always `<field>_<dir>`, so the field set is the only thing that
 * differs per page — it arrives as the label map the page already keeps for its
 * column headers.
 */

export type SortDirection = "asc" | "desc";

export interface SortState<F extends string> {
  field: F;
  dir: SortDirection;
}

export function toSortParam<F extends string>({
  field,
  dir,
}: SortState<F>): string {
  return `${field}_${dir}`;
}

/**
 * Parse `?sort=`, falling back for anything the page or the API rejects.
 *
 * `accepts` is the second gate: a param can name a real field and a real
 * direction and still be a combination the API does not serve.
 */
export function parseSortParam<F extends string>(
  raw: string | null,
  labels: Record<F, string>,
  fallback: SortState<F>,
  accepts: (sort: SortState<F>) => boolean = () => true,
): SortState<F> {
  if (raw === null) return fallback;
  const split = raw.lastIndexOf("_");
  if (split === -1) return fallback;
  const field = raw.slice(0, split) as F;
  const dir = raw.slice(split + 1) as SortDirection;
  if (!labels[field] || (dir !== "asc" && dir !== "desc")) return fallback;
  const sort: SortState<F> = { field, dir };
  return accepts(sort) ? sort : fallback;
}
