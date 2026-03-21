import { AuthView } from "@neondatabase/auth/react/ui";

export function Login() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background">
      <div className="w-full max-w-sm space-y-6 p-6">
        <div className="space-y-2 text-center">
          <h1 className="font-serif text-3xl tracking-tight text-foreground">
            narada
          </h1>
          <p className="text-sm text-muted-foreground">Sign in to continue</p>
        </div>
        <AuthView />
      </div>
    </div>
  );
}
