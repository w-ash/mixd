import { usePutConnectorTokenApiV1ConnectorsServiceTokenPut } from "#/api/generated/connectors/connectors";
import { toasts } from "#/lib/toasts";

/**
 * Connect a `token` auth-method connector with a BYO personal access token.
 *
 * Mirrors the Anthropic-key pattern (`useAssistantKey`): the token is
 * write-only — validated live by the connector, stored encrypted, never
 * echoed back. Validation errors (400) surface on `connectError` for inline
 * display in the token form rather than a toast.
 *
 * Success ordering: `meta.awaitInvalidation` holds the mutation open until the
 * connectors refetch lands, so a caller that `await`s `connect(...)` only
 * proceeds once the card data is current.
 *
 * Disconnect rides the generic `DELETE /connectors/{service}/token` flow via
 * `useConnectorAuth` — no per-connector removal here.
 */
export function useTokenConnect(service: string, displayName: string) {
  const connectMutation = usePutConnectorTokenApiV1ConnectorsServiceTokenPut({
    mutation: {
      onSuccess: () => toasts.success(`${displayName} connected`),
      // Inline error on the form; suppress the global toast.
      meta: { suppressErrorToast: true, awaitInvalidation: true },
    },
  });

  return {
    connect: (token: string) =>
      connectMutation.mutateAsync({ service, data: { token } }),
    isConnecting: connectMutation.isPending,
    connectError: connectMutation.error,
    resetConnect: () => connectMutation.reset(),
  };
}
