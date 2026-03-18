import { AlertTriangle } from "lucide-react";

import {
  DatabaseUnavailable,
  isDatabaseUnavailable,
} from "./DatabaseUnavailable";
import { EmptyState } from "./EmptyState";

/**
 * Unified error state for Tanstack Query failures.
 *
 * Handles two cases:
 * - Database unavailable → dedicated reload prompt
 * - Other errors → generic alert with error message
 */
export function QueryErrorState({
  error,
  heading,
}: {
  error: unknown;
  heading: string;
}) {
  if (isDatabaseUnavailable(error)) {
    return <DatabaseUnavailable />;
  }

  return (
    <EmptyState
      icon={<AlertTriangle className="size-10" />}
      heading={heading}
      description={
        error instanceof Error ? error.message : "An unexpected error occurred."
      }
      role="alert"
    />
  );
}
