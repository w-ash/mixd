import { useAuthenticate } from "@neondatabase/auth/react/ui";
import type { ReactNode } from "react";
import { Navigate } from "react-router";

import { Skeleton } from "@/components/ui/skeleton";

/**
 * Redirects unauthenticated users to /auth/sign-in.
 * Only mounted when authEnabled is true (VITE_NEON_AUTH_URL is set).
 */
export function AuthGuard({ children }: { children: ReactNode }) {
  const { data, isPending } = useAuthenticate();

  if (isPending) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Skeleton className="h-8 w-32" />
      </div>
    );
  }

  if (!data) {
    return <Navigate to="/auth/sign-in" replace />;
  }

  return children;
}
