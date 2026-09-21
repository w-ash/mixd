/**
 * URL-backed pagination.
 *
 * `?page=` is the single source of truth, so a paged view is shareable and
 * survives a reload. {@link usePagination} covers plain offset paging;
 * {@link useKeysetPagination} adds the cursor bookkeeping a keyset endpoint
 * needs, so a list page does not reimplement it.
 */

import { useCallback, useRef, useState } from "react";
import { useSearchParams } from "react-router";

const PAGE_PARAM = "page";

interface UsePaginationOptions {
  defaultLimit?: number;
}

interface UsePaginationResult {
  /** 1-indexed current page, clamped to [1, totalPages] for UI display */
  page: number;
  /** Items per page */
  limit: number;
  /**
   * API offset, derived from the raw URL page and deliberately NOT clamped
   * against `totalPages`: a deep link has to fire the right query on a cold
   * load, before any total is known.
   */
  offset: number;
  /** Total pages derived from total + limit, minimum 1 */
  totalPages: number;
  /** Navigate to a page — updates ?page= in URL, removes param for page 1 */
  setPage: (page: number) => void;
}

/** The slice of a list response this module reads. */
interface KeysetResponse {
  total?: number | null;
  next_cursor?: string | null;
}

interface UseKeysetPaginationResult extends UsePaginationResult {
  /** Cursor for the requested page, once a previous response has supplied one. */
  cursor: string | undefined;
  /**
   * Record the list response. Its `total` drives the controls; its
   * `next_cursor` becomes the cursor for the page after the current one.
   * Call it during render, right after the query.
   */
  rememberNextCursor: (response: KeysetResponse | undefined) => void;
  /** Drop every cached cursor — they describe a result set that no longer exists. */
  resetCursors: () => void;
}

/** The raw `?page=` value, coerced to a whole page at or above 1. */
function usePageParam(): { rawPage: number; setPage: (page: number) => void } {
  const [searchParams, setSearchParams] = useSearchParams();

  const raw = Number(searchParams.get(PAGE_PARAM) ?? "1");
  const rawPage = Number.isFinite(raw) && raw >= 1 ? raw : 1;

  const setPage = useCallback(
    (nextPage: number) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (nextPage <= 1) {
            next.delete(PAGE_PARAM);
          } else {
            next.set(PAGE_PARAM, String(nextPage));
          }
          return next;
        },
        { replace: false },
      );
    },
    [setSearchParams],
  );

  return { rawPage, setPage };
}

function derive(
  rawPage: number,
  total: number,
  limit: number,
): Pick<UsePaginationResult, "page" | "offset" | "totalPages"> {
  const totalPages = total > 0 ? Math.ceil(total / limit) : 1;
  return {
    // Display page is clamped so UI controls are always valid.
    page: Math.max(1, Math.min(rawPage, totalPages)),
    offset: (rawPage - 1) * limit,
    totalPages,
  };
}

export function usePagination(
  total: number,
  { defaultLimit = 50 }: UsePaginationOptions = {},
): UsePaginationResult {
  const { rawPage, setPage } = usePageParam();
  return {
    ...derive(rawPage, total, defaultLimit),
    limit: defaultLimit,
    setPage,
  };
}

/**
 * Pagination for a keyset endpoint.
 *
 * Sequential navigation follows the cursor each response hands back rather than
 * a deepening offset scan; a page nobody has stepped through yet — a deep link,
 * a jump — falls back to the offset. The cursors describe one result set, so a
 * filter write must call {@link UseKeysetPaginationResult.resetCursors}.
 *
 * `total` arrives through `rememberNextCursor` rather than as an argument: the
 * query that reports it needs this hook's `offset` and `cursor` to fire at all.
 */
export function useKeysetPagination({
  defaultLimit = 50,
}: UsePaginationOptions = {}): UseKeysetPaginationResult {
  const { rawPage, setPage } = usePageParam();
  // Page number → cursor for the page AFTER it.
  const cursorsRef = useRef<Map<number, string>>(new Map());
  const [total, setTotal] = useState(0);

  const rememberNextCursor = useCallback(
    (response: KeysetResponse | undefined) => {
      // A same-value write to the memo of cursors, so repeating it on a
      // discarded or StrictMode-doubled render changes nothing.
      const next = response?.next_cursor;
      if (next) cursorsRef.current.set(rawPage, next);
      // The backend only sends `total` on the first page of a result set
      // (`include_total=not has_cursor`) — a cursor page reports `total: null`.
      // Keep the last known total rather than collapsing it to 0, or
      // `totalPages` would drop to 1 and the pagination controls would unmount
      // out from under a user who just paged forward.
      // Setting state during render: React re-runs this component with the new
      // total before committing, so the controls below never render a stale
      // page count.
      const nextTotal = response?.total;
      if (nextTotal != null && nextTotal !== total) setTotal(nextTotal);
    },
    [rawPage, total],
  );

  const resetCursors = useCallback(() => {
    cursorsRef.current.clear();
    // A new filter describes a new result set with its own total — the old
    // one must not leak into it.
    setTotal(0);
  }, []);

  return {
    ...derive(rawPage, total, defaultLimit),
    limit: defaultLimit,
    cursor: cursorsRef.current.get(rawPage - 1),
    setPage,
    rememberNextCursor,
    resetCursors,
  };
}
