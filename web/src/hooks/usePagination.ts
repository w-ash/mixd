import { useCallback } from "react";
import { useSearchParams } from "react-router";

interface UsePaginationOptions {
  defaultLimit?: number;
}

interface UsePaginationResult {
  /** 1-indexed current page, clamped to [1, totalPages] for UI display */
  page: number;
  /** Items per page */
  limit: number;
  /** API offset — derived from raw URL page (not clamped), so queries fire correctly on cold load */
  offset: number;
  /** Total pages derived from total + limit, minimum 1 */
  totalPages: number;
  /** Navigate to a page — updates ?page= in URL, removes param for page 1 */
  setPage: (page: number) => void;
}

export function usePagination(
  total: number,
  { defaultLimit = 50 }: UsePaginationOptions = {},
): UsePaginationResult {
  const [searchParams, setSearchParams] = useSearchParams();

  const limit = defaultLimit;
  const totalPages = total > 0 ? Math.ceil(total / limit) : 1;

  // Raw page from URL — used for offset (not clamped against totalPages)
  const rawPage = Number(searchParams.get("page") ?? "1");
  const sanitizedRaw = Number.isFinite(rawPage) && rawPage >= 1 ? rawPage : 1;

  // Offset uses raw page so deep-links work before total is known
  const offset = (sanitizedRaw - 1) * limit;

  // Display page is clamped so UI controls are always valid
  const page = Math.max(1, Math.min(sanitizedRaw, totalPages));

  const setPage = useCallback(
    (nextPage: number) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (nextPage <= 1) {
            next.delete("page");
          } else {
            next.set("page", String(nextPage));
          }
          return next;
        },
        { replace: false },
      );
    },
    [setSearchParams],
  );

  return { page, limit, offset, totalPages, setPage };
}
