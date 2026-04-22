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

  const setFilter = useCallback(
    (key: string, value: string | null) => {
      onMutate?.();
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (value === null || value === "") {
            next.delete(key);
          } else {
            next.set(key, value);
          }
          next.delete("page");
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams, onMutate],
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

  return { searchParams, setFilter, setMultiFilter, clearAll };
}
