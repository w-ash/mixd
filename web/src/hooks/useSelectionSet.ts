/**
 * Checkbox multi-select over a list of ids.
 *
 * Backs the "select rows, then act on them" pattern shared by the library
 * table, the playlist track editor and the connector playlist picker.
 *
 * Two properties matter beyond the obvious add/remove:
 *
 * - **Stale ids never escape.** `selected` is the raw selection intersected
 *   with `ids`, derived each render rather than synced by an effect. A
 *   background refetch that drops a row cannot inflate "N selected", falsely
 *   satisfy "select all" (size match is not id match), or ride along in an
 *   all-or-nothing batch mutation where one unknown id fails the whole call.
 *   Ids that come back — a filter widening, a refetch restoring a row — are
 *   still selected, because the raw set kept them.
 * - **No-op toggles allocate nothing.** Radix re-fires `onCheckedChange` even
 *   when the state has not drifted; every mutator returns the previous set
 *   when it would not change it, so consumers keep referential stability.
 *
 * `ids` is everything the caller knows about — the pruning set. A caller that
 * also filters its rows passes the visible subset as `visibleIds`, so
 * `toggleAll` and `headerChecked` speak for the rows on screen while the
 * selection itself survives a filter change. Memoize both arrays where the
 * caller can — a fresh array each render recomputes the intersection, which is
 * O(selection size).
 */

import { useCallback, useMemo, useState } from "react";

export interface SelectionSet {
  /** Selected ids that still exist in `ids`. */
  selected: ReadonlySet<string>;
  /** Selected ids across the whole list, visible or not. */
  size: number;
  /** Selected ids among `visibleIds`. Equals `size` when nothing is filtered. */
  visibleSize: number;
  isSelected: (id: string) => boolean;
  /** Flip `id`, or force it to `next` when a checkbox reports its own state. */
  toggle: (id: string, next?: boolean) => void;
  /** Select every visible id, or deselect them all when they already are. */
  toggleAll: () => void;
  /** Drop the whole selection, including ids not currently visible. */
  clear: () => void;
  /** Tri-state for a "select all" header checkbox, scoped to the visible ids. */
  headerChecked: boolean | "indeterminate";
}

export interface SelectionSetOptions {
  /** Rows currently on screen. Defaults to `ids` (nothing filtered out). */
  visibleIds?: readonly string[];
}

const EMPTY: ReadonlySet<string> = new Set();

export function useSelectionSet(
  ids: readonly string[],
  { visibleIds }: SelectionSetOptions = {},
): SelectionSet {
  const [raw, setRaw] = useState<ReadonlySet<string>>(EMPTY);

  const idSet = useMemo(() => new Set(ids), [ids]);
  const visibleSet = useMemo(
    () => (visibleIds === undefined ? idSet : new Set(visibleIds)),
    [visibleIds, idSet],
  );

  const selected = useMemo(() => {
    if (raw.size === 0) return EMPTY;
    const kept = new Set<string>();
    for (const id of raw) if (idSet.has(id)) kept.add(id);
    // Nothing pruned: hand back the raw set so its identity survives.
    return kept.size === raw.size ? raw : kept;
  }, [raw, idSet]);

  const toggle = useCallback((id: string, next?: boolean) => {
    setRaw((prev) => {
      const has = prev.has(id);
      const want = next ?? !has;
      if (want === has) return prev;
      const updated = new Set(prev);
      if (want) updated.add(id);
      else updated.delete(id);
      return updated;
    });
  }, []);

  const toggleAll = useCallback(() => {
    setRaw((prev) => {
      let allSelected = visibleSet.size > 0;
      for (const id of visibleSet) {
        if (!prev.has(id)) {
          allSelected = false;
          break;
        }
      }
      const updated = new Set(prev);
      for (const id of visibleSet) {
        if (allSelected) updated.delete(id);
        else updated.add(id);
      }
      return updated.size === prev.size ? prev : updated;
    });
  }, [visibleSet]);

  const clear = useCallback(() => {
    setRaw((prev) => (prev.size === 0 ? prev : EMPTY));
  }, []);

  const isSelected = useCallback((id: string) => selected.has(id), [selected]);

  const visibleSize = useMemo(() => {
    if (visibleSet === idSet) return selected.size;
    let count = 0;
    for (const id of selected) if (visibleSet.has(id)) count++;
    return count;
  }, [selected, visibleSet, idSet]);

  const headerChecked: boolean | "indeterminate" =
    visibleSet.size === 0 || visibleSize === 0
      ? false
      : visibleSize === visibleSet.size
        ? true
        : "indeterminate";

  return {
    selected,
    size: selected.size,
    visibleSize,
    isSelected,
    toggle,
    toggleAll,
    clear,
    headerChecked,
  };
}
