import type { FallbackProps } from "react-error-boundary";

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
      <div className="mt-2">
        <button
          type="button"
          onClick={resetErrorBoundary}
          className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-surface transition-colors hover:bg-primary/80"
        >
          Try again
        </button>
      </div>
    </div>
  );
}
