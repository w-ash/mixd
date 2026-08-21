import { useCallback } from "react";
import { useSearchParams } from "react-router";

interface UseFilterStateOptions {
  /** Called on every mutation so callers can reset page-scoped state
   *  (e.g., clear the keyset-cursor cache, drop the multi-select). */
  onMutate?: () => void;
}

/**
 * Shared handlers for URL-driven filter state.
 *
 * Library.tsx historically open-coded this pattern in four places
 * (setFilter, setTagFilters, handleSearchChange, onClearAll). Each call
 * site needed to: write to searchParams, drop the `page` param, and run
 * the same "reset page-local state" hook afterward. This hook centralizes
 * the URL write + page reset + onMutate callback so every call site
 * stays consistent.
 *
 * State that lives outside the URL (cursor cache, selection set) is
 * cleared by the caller's `onMutate` — keeps this hook pure with respect
 * to things it doesn't own.
 */
export function useFilterState({ onMutate }: UseFilterStateOptions = {}) {
  const [searchParams, setSearchParams] = useSearchParams();

  /**
   * Write several params in ONE navigation.
   *
   * `setSearchParams` does not queue the way `setState` does: react-router
   * builds the updater's input from the `searchParams` captured at the
   * current render and navigates immediately. Two back-to-back calls
   * therefore both branch off the same base and the second silently
   * discards the first. Anything touching more than one key must go
   * through here rather than calling `setFilter` twice.
   */
  const setFilters = useCallback(
    (updates: Record<string, string | null>) => {
      onMutate?.();
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          for (const [key, value] of Object.entries(updates)) {
            if (value === null || value === "") {
              next.delete(key);
            } else {
              next.set(key, value);
            }
          }
          next.delete("page");
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams, onMutate],
  );

  const setFilter = useCallback(
    (key: string, value: string | null) => setFilters({ [key]: value }),
    [setFilters],
  );

  const setMultiFilter = useCallback(
    (key: string, values: string[]) => {
      onMutate?.();
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          next.delete(key);
          for (const v of values) next.append(key, v);
          next.delete("page");
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams, onMutate],
  );

  const clearAll = useCallback(() => {
    onMutate?.();
    setSearchParams(() => new URLSearchParams(), { replace: true });
  }, [setSearchParams, onMutate]);

  return { searchParams, setFilter, setFilters, setMultiFilter, clearAll };
}
