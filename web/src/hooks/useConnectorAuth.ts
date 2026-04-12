import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import {
  getLastfmAuthUrlApiV1ConnectorsLastfmAuthUrlGet,
  getSpotifyAuthUrlApiV1ConnectorsSpotifyAuthUrlGet,
} from "#/api/generated/auth/auth";
import {
  getGetConnectorsApiV1ConnectorsGetQueryKey,
  useDeleteConnectorTokenApiV1ConnectorsServiceTokenDelete,
} from "#/api/generated/connectors/connectors";
import { getConnectorLabel } from "#/components/shared/ConnectorIcon";
import { toasts } from "#/lib/toasts";

type AuthUrlFetcher = (
  options?: RequestInit,
) => Promise<{ data: Record<string, string> }>;

const authUrlFetchers: Record<string, AuthUrlFetcher> = {
  spotify:
    getSpotifyAuthUrlApiV1ConnectorsSpotifyAuthUrlGet as unknown as AuthUrlFetcher,
  lastfm:
    getLastfmAuthUrlApiV1ConnectorsLastfmAuthUrlGet as unknown as AuthUrlFetcher,
};

/**
 * Hook for connector OAuth connect/disconnect flows.
 *
 * - `connect()`: fetches the auth URL, then redirects the browser to the provider.
 * - `disconnect()`: DELETEs the stored token and invalidates the connectors query.
 */
export function useConnectorAuth(service: string) {
  const [isConnecting, setIsConnecting] = useState(false);
  const queryClient = useQueryClient();
  const label = getConnectorLabel(service);
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
    const fetcher = authUrlFetchers[service];
    if (!fetcher) return;

    setIsConnecting(true);
    try {
      const response = await fetcher();
      const authUrl = response.data.auth_url;
      if (authUrl) {
        window.location.href = authUrl;
        // Don't reset isConnecting — page is navigating away
        return;
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
