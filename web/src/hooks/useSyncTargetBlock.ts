import { useCallback } from "react";

import type { getConnectorsApiV1ConnectorsGetResponse } from "#/api/generated/connectors/connectors";
import { useGetConnectorsApiV1ConnectorsGet } from "#/api/generated/connectors/connectors";
import type {
  ConnectorMetadataSchema,
  SyncTargetSchema,
  SyncTargetSchemaId,
} from "#/api/generated/model";
import type { listSyncTargetsApiV1SyncTargetsGetResponse } from "#/api/generated/schedules/schedules";
import { useListSyncTargetsApiV1SyncTargetsGet } from "#/api/generated/schedules/schedules";
import { STALE } from "#/api/query-client";
import { getConnectorLabel } from "#/lib/connector-brand";

/**
 * The server's record for one background-sync target, or null.
 *
 * `/sync/targets` is per user: it carries the cadence owner (`self_managed`)
 * and the same credential verdict the trigger routes 409 on (`available` plus
 * `blocked_reason`), so a card never has to reconstruct either.
 *
 * Null both while the list loads and for a target the server does not list —
 * an unknown target must not be given a scheduler or a blocked hint.
 */
function useSyncTarget(
  targetId: SyncTargetSchemaId | undefined,
): SyncTargetSchema | null {
  const select = useCallback(
    (res: listSyncTargetsApiV1SyncTargetsGetResponse) =>
      res.data.data.find((target) => target.id === targetId) ?? null,
    [targetId],
  );
  const { data } = useListSyncTargetsApiV1SyncTargetsGet({
    // The route's cache tag also reads `connectors`, so a connect or disconnect
    // refetches it; between those it is static.
    query: { select, staleTime: STALE.STATIC },
  });
  return data ?? null;
}

/** Connector metadata by registry name — the key `SyncTargetSchema.service` uses. */
const selectConnectorsByName = (
  res: getConnectorsApiV1ConnectorsGetResponse,
): ReadonlyMap<string, ConnectorMetadataSchema> =>
  new Map(res.data.map((connector) => [connector.name, connector]));

/** A reason a sync target cannot run, and how to render it. */
export interface SyncTargetBlock {
  text: string;
  className: string;
}

function connectHint(label: string, actionVerb: string): SyncTargetBlock {
  return {
    text: `Connect ${label} in Integrations to ${actionVerb}.`,
    className: "text-text-faint",
  };
}

/**
 * What blocks a sync target, said as the action that unblocks it.
 *
 * Two sources, because neither is complete alone. `/sync/targets` judges the
 * stored credential — present, and granted the scopes this target needs — but
 * never calls the service, so a revoked grant still reads as available there.
 * `/connectors` carries the live status probe, which is what reports a dead
 * token as `connected: false`. Reading both keeps the trigger disabled for work
 * the importer would fail, not just for work the API would refuse.
 *
 * Both queries share the `connectors` cache tag, so connecting or disconnecting
 * refetches the pair together.
 *
 * `actionVerb` names what the card's own button does ("import", "export") so the
 * hint and the button agree.
 */
export function useSyncTargetBlock(
  targetId: SyncTargetSchemaId | undefined,
  actionVerb: string,
): { target: SyncTargetSchema | null; block: SyncTargetBlock | null } {
  const target = useSyncTarget(targetId);
  const { data: connectorsByName } = useGetConnectorsApiV1ConnectorsGet({
    query: { select: selectConnectorsByName, staleTime: STALE.STATIC },
  });
  return { target, block: blockFor(target, connectorsByName, actionVerb) };
}

function blockFor(
  target: SyncTargetSchema | null,
  connectorsByName: ReadonlyMap<string, ConnectorMetadataSchema> | undefined,
  actionVerb: string,
): SyncTargetBlock | null {
  if (target === null) return null;
  const label = getConnectorLabel(target.service);

  // Undefined while the probe is in flight, and for a service the registry does
  // not carry. Optimistic in both cases: a pending answer must not flicker every
  // trigger disabled.
  const connector = connectorsByName?.get(target.service);
  if (connector?.connected === false) return connectHint(label, actionVerb);

  if (target.available) return null;
  if (target.blocked_reason === "CONNECTOR_SCOPE_MISSING") {
    // A scope gap keeps the connector connected — its other surfaces still work
    // — so only the target needing the new scope says this.
    return {
      text: `Reconnect ${label} in Integrations to grant recently-played access.`,
      className: "text-status-expired",
    };
  }
  return connectHint(label, actionVerb);
}
