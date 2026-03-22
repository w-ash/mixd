import { AuthView } from "@neondatabase/auth/react/ui";
import { useParams } from "react-router";

import { MixdLogo } from "@/components/shared/MixdLogo";

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
        <AuthView pathname={pathname} />
      </div>
    </div>
  );
}
