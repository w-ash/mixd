import { HelpCircle } from "lucide-react";
import { useGetConnectorsApiV1ConnectorsGet } from "@/api/generated/connectors/connectors";
import type { ConnectorStatusSchema } from "@/api/generated/model";
import { PageHeader } from "@/components/layout/PageHeader";
import { ConnectorCard } from "@/components/shared/ConnectorCard";
import { EmptyState } from "@/components/shared/EmptyState";
import { QueryErrorState } from "@/components/shared/QueryErrorState";
import { SectionHeader } from "@/components/shared/SectionHeader";
import { Skeleton } from "@/components/ui/skeleton";

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

function IntegrationsSkeleton() {
  return (
    <div className="space-y-8">
      {[2, 2].map((count, i) => (
        // biome-ignore lint/suspicious/noArrayIndexKey: static skeleton placeholders
        <div key={i} className="space-y-3">
          <Skeleton className="h-3 w-32" />
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {Array.from({ length: count }).map((_, j) => (
              <div
                // biome-ignore lint/suspicious/noArrayIndexKey: static skeleton placeholders
                key={j}
                className="rounded-xl border border-border p-4 space-y-2"
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <Skeleton className="size-8 rounded" />
                    <Skeleton className="h-4 w-20" />
                  </div>
                  <Skeleton className="h-5 w-16 rounded-full" />
                </div>
                <Skeleton className="h-3.5 w-full max-w-48" />
                <Skeleton className="h-3.5 w-full max-w-36" />
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function ConnectorSection({
  title,
  description,
  connectors,
}: {
  title: string;
  description: string;
  connectors: ConnectorStatusSchema[];
}) {
  if (connectors.length === 0) return null;

  return (
    <div className="space-y-3">
      <SectionHeader title={title} description={description} />
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {connectors.map((connector) => (
          <ConnectorCard key={connector.name} connector={connector} />
        ))}
      </div>
    </div>
  );
}

export function Integrations() {
  const { data, isLoading, isError, error } =
    useGetConnectorsApiV1ConnectorsGet();

  const connectors = data?.status === 200 ? data.data : [];

  return (
    <div>
      <title>Integrations — Narada</title>
      <PageHeader
        title="Integrations"
        description="Connected services and application preferences."
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
        <div className="space-y-12">
          {sections.map((section) => (
            <ConnectorSection
              key={section.title}
              title={section.title}
              description={section.description}
              connectors={connectors.filter((c) =>
                section.names.includes(c.name),
              )}
            />
          ))}
        </div>
      )}
    </div>
  );
}
