import { usePutDiscogsTokenApiV1ConnectorsDiscogsTokenPut } from "#/api/generated/connectors/connectors";
import { toasts } from "#/lib/toasts";

/**
 * Connect Discogs with a BYO personal access token.
 *
 * Mirrors the Anthropic-key pattern (`useAssistantKey`): the token is
 * write-only — validated live against Discogs, stored encrypted, never
 * echoed back. Validation errors (400) surface on `connectError` for inline
 * display in the token form rather than a toast.
 *
 * Success ordering: `meta.awaitInvalidation` holds the mutation open until the
 * connectors refetch lands, so a caller that `await`s `connect(...)` only
 * proceeds once the card data is current.
 *
 * Disconnect rides the generic `DELETE /connectors/{service}/token` flow
 * via `useConnectorAuth` — no discogs-specific removal here.
 */
export function useDiscogsToken() {
  const connectMutation = usePutDiscogsTokenApiV1ConnectorsDiscogsTokenPut({
    mutation: {
      onSuccess: () => toasts.success("Discogs connected"),
      // `awaitInvalidation` holds the mutation open until the connectors
      // refetch lands, so the toast never announces a card that still reads
      // disconnected. Inline error on the form; suppress the global toast.
      meta: { suppressErrorToast: true, awaitInvalidation: true },
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
