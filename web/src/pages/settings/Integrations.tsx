import { useQueryClient } from "@tanstack/react-query";
import { Disc3, HelpCircle } from "lucide-react";
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
import {
  ConnectorIcon,
  getConnectorLabel,
} from "@/components/shared/ConnectorIcon";
import { EmptyState } from "@/components/shared/EmptyState";
import { QueryErrorState } from "@/components/shared/QueryErrorState";
import { SectionHeader } from "@/components/shared/SectionHeader";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useConnectorAuth } from "@/hooks/useConnectorAuth";
import {
  CONNECTABLE_SERVICES,
  connectButtonStyles,
  humanizeAuthError,
} from "@/lib/connectors";

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
          <div className="grid gap-3 sm:grid-cols-2">
            {Array.from({ length: count }).map((_, j) => (
              <div
                // biome-ignore lint/suspicious/noArrayIndexKey: static skeleton placeholders
                key={j}
                className="rounded-xl border border-border p-5 space-y-3"
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

// ---------------------------------------------------------------------------
// Onboarding hero — shown when no connectable services are connected
// ---------------------------------------------------------------------------

function OnboardingHero() {
  const spotifyAuth = useConnectorAuth("spotify");
  const lastfmAuth = useConnectorAuth("lastfm");

  return (
    <div className="flex flex-col items-center justify-center gap-4 rounded-lg border border-border-muted bg-surface-sunken px-8 py-16 text-center">
      <span
        className="flex size-20 items-center justify-center rounded-full bg-surface-elevated text-primary"
        aria-hidden="true"
      >
        <Disc3 className="size-10" />
      </span>
      <h2 className="font-display text-xl font-medium text-text">
        Bring your music home
      </h2>
      <p className="max-w-md text-sm text-text-muted">
        Connect your streaming services to start building your unified library.
        Your data stays yours — always.
      </p>
      <div className="mt-2 flex flex-col gap-3 sm:flex-row">
        <Button
          onClick={spotifyAuth.connect}
          disabled={spotifyAuth.isConnecting}
          className={`${connectButtonStyles.spotify} min-h-[44px]`}
        >
          <ConnectorIcon name="spotify" iconSize="sm" labelHidden />
          Connect Spotify
        </Button>
        <Button
          onClick={lastfmAuth.connect}
          disabled={lastfmAuth.isConnecting}
          className={`${connectButtonStyles.lastfm} min-h-[44px]`}
        >
          <ConnectorIcon name="lastfm" iconSize="sm" labelHidden />
          Connect Last.fm
        </Button>
      </div>
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
      <div className="grid gap-3 sm:grid-cols-2">
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

  // Check if we should show the onboarding hero:
  // no connectable services are connected
  const showOnboarding =
    !isLoading &&
    !isError &&
    connectors.length > 0 &&
    connectors
      .filter((c) => CONNECTABLE_SERVICES.has(c.name))
      .every((c) => !c.connected);

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

      {showOnboarding && <OnboardingHero />}

      {!isLoading && !isError && connectors.length > 0 && (
        <div className={showOnboarding ? "mt-12 space-y-12" : "space-y-12"}>
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
