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
  description,
}: {
  error: unknown;
  heading: string;
  /** Static override of the default error-message description. */
  description?: string;
}) {
  if (isDatabaseUnavailable(error)) {
    return <DatabaseUnavailable />;
  }

  return (
    <EmptyState
      icon={<AlertTriangle className="size-10" />}
      heading={heading}
      description={
        description ??
        (error instanceof Error
          ? error.message
          : "An unexpected error occurred.")
      }
      role="alert"
    />
  );
}
