import type { ReactNode } from "react";

import { QueryErrorState } from "./QueryErrorState";

interface QueryStatesProps {
  /**
   * The page's existing Tanstack flag, passed verbatim — `isLoading` for
   * placeholder-data pages, `isPending` where that's what the page used.
   * (They differ for `enabled: false` queries, so the wrapper never picks.)
   */
  loading: boolean;
  isError: boolean;
  /** The query's error; forwarded to QueryErrorState. */
  error?: unknown;
  errorHeading: string;
  /** Static override of the error-message description. */
  errorDescription?: string;
  skeleton: ReactNode;
  /** Caller-derived — the wrapper never reaches into response envelopes. */
  isEmpty?: boolean;
  empty?: ReactNode;
  children: ReactNode;
}

/**
 * Canonical four-state ladder for data views: loading → error → empty →
 * success, in that order, exactly one rendered. Replaces the per-page
 * `{isLoading && …} {isError && …} {!isLoading && !isError && …}` chains.
 */
export function QueryStates({
  loading,
  isError,
  error,
  errorHeading,
  errorDescription,
  skeleton,
  isEmpty = false,
  empty,
  children,
}: QueryStatesProps) {
  if (loading) {
    return <>{skeleton}</>;
  }
  if (isError) {
    return (
      <QueryErrorState
        error={error}
        heading={errorHeading}
        description={errorDescription}
      />
    );
  }
  if (isEmpty) {
    return <>{empty}</>;
  }
  return <>{children}</>;
}
