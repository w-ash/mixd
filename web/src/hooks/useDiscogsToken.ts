import { useQueryClient } from "@tanstack/react-query";

import { usePutDiscogsTokenApiV1ConnectorsDiscogsTokenPut } from "#/api/generated/connectors/connectors";
import { settleConnectorsRefetch } from "#/lib/connector-queries";
import { toasts } from "#/lib/toasts";

/**
 * Connect Discogs with a BYO personal access token.
 *
 * Mirrors the Anthropic-key pattern (`useAssistantKey`): the token is
 * write-only — validated live against Discogs, stored encrypted, never
 * echoed back. Validation errors (400) surface on `connectError` for inline
 * display in the token form rather than a toast.
 *
 * Success ordering: `onSuccess` is async and *awaits* the connectors
 * refetch before declaring success. TanStack Query's `mutateAsync` awaits
 * `onSuccess` before its returned promise resolves, so callers that
 * `await connect(...)` (the dialog) only proceed once the card data is
 * current — the toast and the dialog close never race ahead of the refetch
 * that makes them true.
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
