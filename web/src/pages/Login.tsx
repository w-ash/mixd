import { AuthView } from "@neondatabase/auth/react/ui";
import { ErrorBoundary, type FallbackProps } from "react-error-boundary";
import { useParams } from "react-router";

import { MixdLogo } from "@/components/shared/MixdLogo";
import { Button } from "@/components/ui/button";

function AuthErrorFallback({ error, resetErrorBoundary }: FallbackProps) {
  const message =
    error instanceof Error
      ? error.message
      : "Authentication service unavailable";

  return (
    <div role="alert" className="space-y-3 text-center">
      <p className="text-sm text-destructive">{message}</p>
      <Button onClick={resetErrorBoundary}>Try again</Button>
    </div>
  );
}

export function Login() {
  const { pathname } = useParams();

  return (
    <div className="flex min-h-screen items-center justify-center bg-background">
      <div className="w-full max-w-sm space-y-6 p-6">
        <div className="flex flex-col items-center gap-2 text-center">
          <MixdLogo size="lg" />
          <p className="text-sm text-muted-foreground">
            {pathname === "sign-up"
              ? "Create an account to get started"
              : "Sign in to continue"}
          </p>
        </div>
        <ErrorBoundary
          FallbackComponent={AuthErrorFallback}
          resetKeys={[pathname]}
        >
          <AuthView pathname={pathname} />
        </ErrorBoundary>
      </div>
    </div>
  );
}
