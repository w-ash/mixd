import { useAuthenticate } from "@neondatabase/auth/react/ui";
import { AlertTriangle } from "lucide-react";
import type { ReactNode } from "react";
import { Navigate } from "react-router";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

/**
 * Redirects unauthenticated users to /auth/sign-in.
 * Only mounted when authEnabled is true (VITE_NEON_AUTH_URL is set).
 */
export function AuthGuard({ children }: { children: ReactNode }) {
  const { data, isPending, error } = useAuthenticate();

  if (isPending) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Skeleton className="h-8 w-32" />
      </div>
    );
  }

  if (error) {
    return (
      <div
        role="alert"
        className="flex min-h-screen flex-col items-center justify-center gap-3 text-center"
      >
        <AlertTriangle className="size-8 text-destructive" />
        <h2 className="font-display text-lg font-medium text-text">
          Authentication error
        </h2>
        <p className="max-w-sm text-sm text-text-muted">
          {error instanceof Error
            ? error.message
            : "Unable to verify your session. The auth service may be unavailable."}
        </p>
        <Button onClick={() => window.location.reload()}>Try again</Button>
      </div>
    );
  }

  if (!data) {
    return <Navigate to="/auth/sign-in" replace />;
  }

  return children;
}
