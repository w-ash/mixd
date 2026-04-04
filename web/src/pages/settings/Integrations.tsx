import { useQueryClient } from "@tanstack/react-query";
import { HelpCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { toast } from "sonner";

import {
  getGetConnectorsApiV1ConnectorsGetQueryKey,
  useGetConnectorsApiV1ConnectorsGet,
} from "@/api/generated/connectors/connectors";
import type { ConnectorStatusSchema } from "@/api/generated/model";
import { STALE } from "@/api/query-client";
import { PageHeader } from "@/components/layout/PageHeader";
import { ConnectorCard } from "@/components/shared/ConnectorCard";
import { getConnectorLabel } from "@/components/shared/ConnectorIcon";
import { EmptyState } from "@/components/shared/EmptyState";
import { QueryErrorState } from "@/components/shared/QueryErrorState";
import { SectionHeader } from "@/components/shared/SectionHeader";
import { Skeleton } from "@/components/ui/skeleton";
import { humanizeAuthError } from "@/lib/connectors";

const sections = [
  {
    title: "Streaming",
    description: "Sources that own your playlists, likes, and library",
    names: ["spotify", "apple"],
  },
  {
    title: "Data & Enrichment",
    description: "Services that provide play history and metadata",
    names: ["lastfm", "musicbrainz"],
  },
];

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
  connectors: ConnectorStatusSchema[];
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
      toast.success(`${label} connected`);
      queryClient.invalidateQueries({
        queryKey: getGetConnectorsApiV1ConnectorsGetQueryKey(),
      });
    } else {
      const message = reason ? humanizeAuthError(reason) : "Unknown error";
      toast.error(`${label} connection failed`, { description: message });
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
          {sections.map((section) => (
            <ConnectorSection
              key={section.title}
              title={section.title}
              description={section.description}
              connectors={connectors.filter((c) =>
                section.names.includes(c.name),
              )}
              authResult={authResult}
            />
          ))}
        </div>
      )}
    </div>
  );
}
