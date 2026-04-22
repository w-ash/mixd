import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { getConnectorAuthUrlApiV1ConnectorsServiceAuthUrlGet } from "#/api/generated/auth/auth";
import {
  getGetConnectorsApiV1ConnectorsGetQueryKey,
  useDeleteConnectorTokenApiV1ConnectorsServiceTokenDelete,
} from "#/api/generated/connectors/connectors";
import { getConnectorLabel } from "#/lib/connector-brand";
import { toasts } from "#/lib/toasts";

/**
 * Hook for connector OAuth connect/disconnect flows.
 *
 * - `connect()`: fetches the auth URL from `/connectors/{service}/auth-url`
 *   and redirects the browser to the provider.
 * - `disconnect()`: DELETEs the stored token and invalidates the connectors query.
 */
export function useConnectorAuth(service: string, displayName?: string) {
  const [isConnecting, setIsConnecting] = useState(false);
  const queryClient = useQueryClient();
  const label = displayName ?? getConnectorLabel(service);
  const connectorsQueryKey = getGetConnectorsApiV1ConnectorsGetQueryKey();

  const disconnectMutation =
    useDeleteConnectorTokenApiV1ConnectorsServiceTokenDelete({
      mutation: {
        onSuccess: () => {
          queryClient.invalidateQueries({ queryKey: connectorsQueryKey });
          toasts.success(`${label} disconnected`);
        },
        meta: { errorLabel: `Failed to disconnect ${label}` },
      },
    });

  async function connect() {
    setIsConnecting(true);
    try {
      const response =
        await getConnectorAuthUrlApiV1ConnectorsServiceAuthUrlGet(service);
      if (response.status === 200) {
        const authUrl = response.data.auth_url;
        if (authUrl) {
          window.location.href = authUrl;
          // Don't reset isConnecting — page is navigating away
          return;
        }
      }
      toasts.message(`No auth URL returned for ${label}`);
    } catch {
      toasts.message(`Failed to start ${label} authorization`);
    }
    setIsConnecting(false);
  }

  function disconnect() {
    disconnectMutation.mutate({ service });
  }

  return {
    connect,
    disconnect,
    isConnecting,
    isDisconnecting: disconnectMutation.isPending,
  };
}
