import type { FallbackProps } from "react-error-boundary";

import { Button } from "#/components/ui/button";

export function PageErrorFallback({
  error,
  resetErrorBoundary,
}: FallbackProps) {
  const message =
    error instanceof Error ? error.message : "An unexpected error occurred";

  return (
    <div
      role="alert"
      className="flex flex-col items-center justify-center gap-3 rounded-lg border border-border-muted bg-surface-sunken px-8 py-16 text-center"
    >
      <h2 className="font-display text-lg font-medium text-text">
        Something went wrong
      </h2>
      <p className="max-w-sm text-sm text-text-muted">{message}</p>
      <Button onClick={resetErrorBoundary}>Try again</Button>
    </div>
  );
}
