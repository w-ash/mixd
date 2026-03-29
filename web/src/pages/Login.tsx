import { AuthView } from "@neondatabase/auth/react/ui";
import { useEffect } from "react";
import { ErrorBoundary, type FallbackProps } from "react-error-boundary";
import { useParams, useSearchParams } from "react-router";
import { toast } from "sonner";

import { MixdLogo } from "@/components/shared/MixdLogo";
import { Button } from "@/components/ui/button";

const AUTH_ERROR_MESSAGES: Record<string, string> = {
  session_expired: "Your session has expired. Please sign in again.",
};

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
  const [searchParams, setSearchParams] = useSearchParams();

  useEffect(() => {
    const error = searchParams.get("error");
    if (error && AUTH_ERROR_MESSAGES[error]) {
      toast.error(AUTH_ERROR_MESSAGES[error]);
      setSearchParams({}, { replace: true });
    }
  }, [searchParams, setSearchParams]);

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
      <span className="fixed right-3 bottom-3 font-mono text-[10px] text-text-faint/50">
        v{__APP_VERSION__} ({__BUILD_HASH__})
      </span>
    </div>
  );
}
