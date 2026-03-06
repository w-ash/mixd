import { useDeferredValue, useState } from "react";

interface UseTrackSearchResult {
  /** Current input value — updates on every keystroke */
  search: string;
  /** Set the search input value */
  setSearch: (value: string) => void;
  /** Deferred value — lags behind `search` while React renders */
  deferredSearch: string;
  /** True when the deferred value hasn't caught up to the input yet */
  isSearching: boolean;
}

/**
 * Search hook using React 19's `useDeferredValue` for built-in debouncing.
 *
 * The input updates immediately (responsive typing) while the API query
 * uses `deferredSearch`, which React defers during concurrent renders.
 * This avoids firing a request on every keystroke without a manual timer.
 */
export function useTrackSearch(initialValue = ""): UseTrackSearchResult {
  const [search, setSearch] = useState(initialValue);
  const deferredSearch = useDeferredValue(search);
  const isSearching = search !== deferredSearch;

  return { search, setSearch, deferredSearch, isSearching };
}
