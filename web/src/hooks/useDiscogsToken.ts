import type { QueryClient } from "@tanstack/react-query";
import { useQueryClient } from "@tanstack/react-query";

import {
  getGetConnectorsApiV1ConnectorsGetQueryKey,
  getGetConnectorsApiV1ConnectorsGetQueryOptions,
  usePutDiscogsTokenApiV1ConnectorsDiscogsTokenPut,
} from "#/api/generated/connectors/connectors";
import { toasts } from "#/lib/toasts";

/**
 * Refetch the connectors query and wait for it to settle before returning.
 *
 * `invalidateQueries({ refetchType: "active" })` resolves only once the
 * refetches it triggers have settled — that's the ordering primitive this
 * hook leans on so the caller can await "the card is up to date" rather
 * than "the invalidation was requested".
 *
 * Guards the same first-fetch race as the OAuth callback path in
 * `Integrations.tsx`: if the connectors query has never landed data yet
 * (dialog opened before the page's initial GET settled), `invalidateQueries`
 * would just dedupe onto that same in-flight promise and redisplay
 * pre-connect data. `fetchQuery` first forces a real request to exist to
 * invalidate.
 */
async function settleConnectorsRefetch(queryClient: QueryClient) {
  const queryKey = getGetConnectorsApiV1ConnectorsGetQueryKey();
  const hasData = queryClient.getQueryData(queryKey) !== undefined;
  if (!hasData) {
    await queryClient
      .fetchQuery(getGetConnectorsApiV1ConnectorsGetQueryOptions())
      .catch(() => undefined);
  }
  await queryClient.invalidateQueries({
    queryKey,
    refetchType: "active",
  });
}

/**
 * Connect Discogs with a BYO personal access token (v0.11.1).
 *
 * Mirrors the Anthropic-key pattern (`useAssistantKey`): the token is
 * write-only — validated live against Discogs, stored encrypted, never
 * echoed back. Validation errors (400) surface on `connectError` for inline
 * display in the token form rather than a toast.
 *
 * Success ordering (v0.11.x fix): `onSuccess` is async and *awaits* the
 * connectors refetch before declaring success. TanStack Query's
 * `mutateAsync` awaits `onSuccess` before its returned promise resolves, so
 * callers that `await connect(...)` (the dialog) only proceed once the card
 * data is current — the toast and the dialog close never race ahead of the
 * refetch that makes them true.
 *
 * Disconnect rides the generic `DELETE /connectors/{service}/token` flow
 * via `useConnectorAuth` — no discogs-specific removal here.
 */
export function useDiscogsToken() {
  const queryClient = useQueryClient();

  const connectMutation = usePutDiscogsTokenApiV1ConnectorsDiscogsTokenPut({
    mutation: {
      onSuccess: async () => {
        await settleConnectorsRefetch(queryClient);
        toasts.success("Discogs connected");
      },
      // Inline error on the token form; suppress the global error toast.
      meta: { suppressErrorToast: true },
    },
  });

  return {
    connect: (token: string) =>
      connectMutation.mutateAsync({ data: { token } }),
    isConnecting: connectMutation.isPending,
    connectError: connectMutation.error,
    resetConnect: () => connectMutation.reset(),
  };
}
