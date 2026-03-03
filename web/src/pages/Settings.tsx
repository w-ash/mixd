import { useGetConnectorsApiV1ConnectorsGet } from "@/api/generated/connectors/connectors";
import { PageHeader } from "@/components/layout/PageHeader";
import { ConnectorCard } from "@/components/shared/ConnectorCard";
import { EmptyState } from "@/components/shared/EmptyState";
import { Skeleton } from "@/components/ui/skeleton";

function SettingsSkeleton() {
  return (
    <div className="flex flex-col gap-3">
      {Array.from({ length: 4 }).map((_, i) => (
        <div
          // biome-ignore lint/suspicious/noArrayIndexKey: static skeleton placeholders
          key={i}
          className="rounded-lg border border-border p-4 space-y-1.5"
        >
          <div className="flex items-center gap-3">
            <Skeleton className="h-4 w-4 rounded" />
            <Skeleton className="h-4 w-20" />
            <Skeleton className="ml-auto h-5 w-20 rounded-full" />
          </div>
          <Skeleton className="h-3 w-48" />
        </div>
      ))}
    </div>
  );
}

export function Settings() {
  const { data, isLoading, isError, error } =
    useGetConnectorsApiV1ConnectorsGet();

  const connectors = data?.status === 200 ? data.data : [];

  return (
    <div>
      <PageHeader
        title="Settings"
        description="Manage your connected music services."
      />

      {isLoading && <SettingsSkeleton />}

      {isError && (
        <EmptyState
          icon="!"
          heading="Failed to load connectors"
          description={
            error instanceof Error
              ? error.message
              : "An unexpected error occurred."
          }
        />
      )}

      {!isLoading && !isError && connectors.length === 0 && (
        <EmptyState
          icon="?"
          heading="No connectors configured"
          description="No music service connectors are available."
        />
      )}

      {!isLoading && !isError && connectors.length > 0 && (
        <div className="flex flex-col gap-3">
          {connectors.map((connector) => (
            <ConnectorCard key={connector.name} connector={connector} />
          ))}
        </div>
      )}
    </div>
  );
}
