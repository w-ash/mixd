import { useQueryClient } from "@tanstack/react-query";
import { HelpCircle } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
import {
  getGetConnectorsApiV1ConnectorsGetQueryKey,
  useGetConnectorsApiV1ConnectorsGet,
} from "#/api/generated/connectors/connectors";
import type { ConnectorMetadataSchema } from "#/api/generated/model";
import { STALE } from "#/api/query-client";
import { PageHeader } from "#/components/layout/PageHeader";
import { ConnectorCard } from "#/components/shared/ConnectorCard";
import { EmptyState } from "#/components/shared/EmptyState";
import { QueryErrorState } from "#/components/shared/QueryErrorState";
import { SectionHeader } from "#/components/shared/SectionHeader";
import { Skeleton } from "#/components/ui/skeleton";
import { getConnectorLabel } from "#/lib/connector-brand";
import { humanizeAuthError } from "#/lib/connectors";
import { toasts } from "#/lib/toasts";

/** Display copy + ordering for connector categories.
 *
 * The backend declares each connector's ``category`` via its registry
 * entry (``streaming`` / ``history`` / ``enrichment``). This map is
 * pure frontend UX copy — translating the taxonomy into user-facing
 * section titles. Adding a new category requires adding an entry here.
 */
type CategoryKey = ConnectorMetadataSchema["category"];

const categoryDisplay: Record<
  CategoryKey,
  { title: string; description: string; order: number }
> = {
  streaming: {
    title: "Streaming",
    description: "Sources that own your playlists, likes, and library",
    order: 0,
  },
  history: {
    title: "Play history",
    description: "Services that provide listening history and scrobble data",
    order: 1,
  },
  enrichment: {
    title: "Metadata & enrichment",
    description: "Services that identify tracks and enrich their metadata",
    order: 2,
  },
};

// ---------------------------------------------------------------------------
// Auth callback result (persisted in component state for the card error state)
// ---------------------------------------------------------------------------

interface AuthResult {
  service: string;
  status: "success" | "error";
  reason?: string;
}

// ---------------------------------------------------------------------------
// Skeleton loader
// ---------------------------------------------------------------------------

function IntegrationsSkeleton() {
  return (
    <div className="space-y-8">
      {[2, 2].map((count, i) => (
        // biome-ignore lint/suspicious/noArrayIndexKey: static skeleton placeholders
        <div key={i} className="space-y-3">
          <Skeleton className="h-3 w-32" />
          <div className="divide-y divide-border rounded-lg border border-border">
            {Array.from({ length: count }).map((_, j) => (
              <div
                // biome-ignore lint/suspicious/noArrayIndexKey: static skeleton placeholders
                key={j}
                className="flex items-center gap-3 px-4 py-3"
              >
                <Skeleton className="size-6 shrink-0 rounded" />
                <div className="min-w-0 flex-1 space-y-1.5">
                  <Skeleton className="h-3.5 w-24" />
                  <Skeleton className="h-3 w-48" />
                </div>
                <Skeleton className="h-5 w-16 shrink-0 rounded-full" />
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Connector section
// ---------------------------------------------------------------------------

function ConnectorSection({
  title,
  description,
  connectors,
  authResult,
}: {
  title: string;
  description: string;
  connectors: ConnectorMetadataSchema[];
  authResult: AuthResult | null;
}) {
  if (connectors.length === 0) return null;

  return (
    <div className="space-y-3">
      <SectionHeader title={title} description={description} />
      <div className="divide-y divide-border rounded-lg border border-border">
        {connectors.map((connector, index) => (
          <div
            key={connector.name}
            className="animate-fade-up"
            style={{ animationDelay: `${index * 75}ms` }}
          >
            <ConnectorCard
              connector={connector}
              authError={
                authResult?.service === connector.name &&
                authResult.status === "error"
                  ? authResult.reason
                  : undefined
              }
            />
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export function Integrations() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const queryClient = useQueryClient();
  const { data, isLoading, isError, error } =
    useGetConnectorsApiV1ConnectorsGet({
      query: { staleTime: STALE.STATIC },
    });

  const connectors = data?.status === 200 ? data.data : [];

  // Group connectors by their backend-declared category, preserving the
  // display ordering above. New categories silently fall through as no-ops
  // until the frontend adds an entry to `categoryDisplay`.
  const grouped = useMemo(() => {
    const bins = new Map<CategoryKey, ConnectorMetadataSchema[]>();
    for (const c of connectors) {
      const list = bins.get(c.category) ?? [];
      list.push(c);
      bins.set(c.category, list);
    }
    return [...bins.entries()]
      .filter(([key]) => key in categoryDisplay)
      .sort(([a], [b]) => categoryDisplay[a].order - categoryDisplay[b].order);
  }, [connectors]);

  // Auth callback result — persisted so the card can show error state
  const [authResult, setAuthResult] = useState<AuthResult | null>(null);

  // Handle auth callback query params (e.g. ?auth=spotify&status=success)
  useEffect(() => {
    const service = searchParams.get("auth");
    const status = searchParams.get("status") as "success" | "error" | null;

    if (!service || !status) return;

    const reason = searchParams.get("reason") ?? undefined;
    const label = getConnectorLabel(service);

    if (status === "success") {
      toasts.success(`${label} connected`);
      queryClient.invalidateQueries({
        queryKey: getGetConnectorsApiV1ConnectorsGetQueryKey(),
      });
    } else {
      const message = reason ? humanizeAuthError(reason) : "Unknown error";
      toasts.message(`${label} connection failed`, { description: message });
      setAuthResult({ service, status, reason });
    }

    // Clean the URL — remove query params without adding a history entry
    navigate("/settings/integrations", { replace: true });
  }, [searchParams, navigate, queryClient]);

  return (
    <div>
      <title>Integrations — Mixd</title>
      <PageHeader
        title="Integrations"
        description="Your music services. Connect streaming services and data sources to build your unified library."
      />

      {isLoading && <IntegrationsSkeleton />}

      {isError && (
        <QueryErrorState error={error} heading="Failed to load connectors" />
      )}

      {!isLoading && !isError && connectors.length === 0 && (
        <EmptyState
          icon={<HelpCircle className="size-10" />}
          heading="No connectors configured"
          description="No music service connectors are available."
        />
      )}

      {!isLoading && !isError && connectors.length > 0 && (
        <div className="space-y-8">
          {grouped.map(([category, sectionConnectors]) => (
            <ConnectorSection
              key={category}
              title={categoryDisplay[category].title}
              description={categoryDisplay[category].description}
              connectors={sectionConnectors}
              authResult={authResult}
            />
          ))}
        </div>
      )}
    </div>
  );
}
