import type { QueryClient } from "@tanstack/react-query";

import {
  getGetConnectorsApiV1ConnectorsGetQueryKey,
  getGetConnectorsApiV1ConnectorsGetQueryOptions,
} from "#/api/generated/connectors/connectors";

/**
 * Refetch the connectors query and wait for it to settle before returning.
 *
 * `invalidateQueries({ refetchType: "active" })` resolves only once the
 * refetches it triggers have settled — that's the ordering primitive this
 * helper leans on so the caller can await "the card is up to date" rather
 * than "the invalidation was requested".
 *
 * Guards the first-fetch race: if the connectors query has never landed
 * data yet (connect finished before the page's initial GET settled),
 * `invalidateQueries` would just dedupe onto that same in-flight promise
 * and redisplay pre-connect data. `fetchQuery` first forces a real request
 * to exist to invalidate.
 *
 * Shared by every connect flow that must not declare success before the
 * card reflects it (`useDiscogsToken`, `useAppleMusicConnect`, the OAuth
 * callback in `Integrations.tsx`).
 */
export async function settleConnectorsRefetch(queryClient: QueryClient) {
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
