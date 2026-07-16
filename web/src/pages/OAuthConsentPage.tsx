import { useState } from "react";
import { useSearchParams } from "react-router";

import {
  useApproveConsentApiV1OauthConsentRequestIdApprovePost,
  useDenyConsentApiV1OauthConsentRequestIdDenyPost,
  useGetConsentDetailsApiV1OauthConsentRequestIdGet,
} from "#/api/generated/oauth/oauth";
import { QueryStates } from "#/components/shared/QueryStates";
import { Button } from "#/components/ui/button";
import { Skeleton } from "#/components/ui/skeleton";

/**
 * OAuth consent for external MCP clients (v0.9.5).
 *
 * An agent (Claude Code, Cursor, …) hit `/authorize` and was redirected here
 * with a `request_id`; the user — already signed in via the surrounding
 * AuthGuard — decides whether that client may act as them on their library.
 * Approve/deny returns a `redirect_url` carrying the authorization code (or
 * `access_denied`) back to the client's local callback; the code is bound
 * server-side to THIS session's user, never chosen by the client.
 */
export function OAuthConsentPage() {
  const [searchParams] = useSearchParams();
  const requestId = searchParams.get("request_id") ?? "";
  const [decided, setDecided] = useState(false);

  const { data, isLoading, isError, error } =
    useGetConsentDetailsApiV1OauthConsentRequestIdGet(requestId, {
      query: { enabled: requestId !== "", retry: false },
    });
  const approve = useApproveConsentApiV1OauthConsentRequestIdApprovePost();
  const deny = useDenyConsentApiV1OauthConsentRequestIdDenyPost();

  const details = data?.status === 200 ? data.data : undefined;
  const busy = approve.isPending || deny.isPending || decided;

  const decide = async (action: "approve" | "deny") => {
    const mutation = action === "approve" ? approve : deny;
    const result = await mutation.mutateAsync({ requestId });
    if (result.status === 200) {
      setDecided(true);
      // Hand the browser to the client's callback — this leaves the app.
      window.location.assign(result.data.redirect_url);
    }
  };

  if (!requestId) {
    return (
      <ConsentShell>
        <p className="font-serif text-muted-foreground">
          This consent link is incomplete — it names no authorization request.
          Retry the connection from your MCP client.
        </p>
      </ConsentShell>
    );
  }

  return (
    <ConsentShell>
      <QueryStates
        loading={isLoading}
        isError={isError}
        error={error}
        errorHeading="This authorization request has expired"
        skeleton={
          <div className="space-y-4">
            <Skeleton className="h-6 w-48" />
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        }
      >
        {details && (
          <div className="space-y-6">
            <div className="space-y-2">
              <p className="font-serif text-lg text-foreground">
                <span className="font-semibold">
                  {details.client_name ?? "An MCP client"}
                </span>{" "}
                wants to connect to your mixd library.
              </p>
              <p className="font-serif text-sm text-muted-foreground">
                If you approve, it can read your library and propose changes as
                you — every write still requires an explicit confirmation, and
                you can revoke access by disconnecting it in the client.
              </p>
            </div>

            <dl className="space-y-1 rounded-md border border-border bg-card/50 p-4 font-mono text-xs text-muted-foreground">
              <div className="flex justify-between gap-4">
                <dt>client</dt>
                <dd className="truncate text-foreground">
                  {details.client_id}
                </dd>
              </div>
              <div className="flex justify-between gap-4">
                <dt>returns to</dt>
                <dd className="truncate text-foreground">
                  {details.redirect_uri}
                </dd>
              </div>
              {details.resource && (
                <div className="flex justify-between gap-4">
                  <dt>resource</dt>
                  <dd className="truncate text-foreground">
                    {details.resource}
                  </dd>
                </div>
              )}
            </dl>

            <div className="flex gap-3">
              <Button
                className="flex-1"
                disabled={busy}
                onClick={() => void decide("approve")}
              >
                {approve.isPending || decided ? "Connecting…" : "Approve"}
              </Button>
              <Button
                variant="outline"
                className="flex-1"
                disabled={busy}
                onClick={() => void decide("deny")}
              >
                Deny
              </Button>
            </div>
          </div>
        )}
      </QueryStates>
    </ConsentShell>
  );
}

function ConsentShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-svh items-center justify-center bg-background p-6">
      <title>Authorize access — Mixd</title>
      <div className="w-full max-w-md space-y-6">
        <h1 className="font-display text-3xl font-bold tracking-tight text-foreground">
          Authorize access
        </h1>
        {children}
      </div>
    </div>
  );
}
