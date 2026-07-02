import { Headphones, Heart, ListMusic, Music, Sparkles } from "lucide-react";
import type { ReactNode } from "react";
import { useGetConnectorsApiV1ConnectorsGet } from "#/api/generated/connectors/connectors";
import type { DashboardStatsSchema } from "#/api/generated/model";
import {
  useGetDashboardStatsApiV1StatsDashboardGet,
  useGetMatchingHealthApiV1StatsMatchingGet,
} from "#/api/generated/stats/stats";
import { STALE } from "#/api/query-client";
import { GettingStarted } from "#/components/dashboard/GettingStarted";
import {
  MatchingHealth,
  MatchingHealthSkeleton,
} from "#/components/dashboard/MatchingHealth";
import { PageHeader } from "#/components/layout/PageHeader";
import { ConnectorIcon } from "#/components/shared/ConnectorIcon";
import { PreferenceBadge } from "#/components/shared/PreferenceToggle";
import { QueryStates } from "#/components/shared/QueryStates";
import { ScheduleFailuresBanner } from "#/components/shared/ScheduleFailuresBanner";
import { CardGridSkeleton } from "#/components/shared/skeletons";
import { formatCount } from "#/lib/format";
import { pluralize } from "#/lib/pluralize";
import { cn } from "#/lib/utils";

/* ── Stat card ───────────────────────────────────────────── */

interface StatCardProps {
  icon: ReactNode;
  label: string;
  value: number;
  hero?: boolean;
  breakdown?: Record<string, number>;
  delay?: string;
}

function StatCard({
  icon,
  label,
  value,
  hero = false,
  breakdown,
  delay,
}: StatCardProps) {
  const hasBreakdown = breakdown && Object.keys(breakdown).length > 0;

  return (
    <article
      className={cn(
        "animate-fade-up rounded-xl border p-5 space-y-1",
        hero
          ? "border-l-2 border-l-primary border-t-border-muted border-r-border-muted border-b-border-muted bg-surface-elevated shadow-md"
          : "border-border-muted bg-surface",
      )}
      style={delay ? { animationDelay: delay } : undefined}
    >
      <span className="text-text-faint">{icon}</span>
      <p className="font-mono text-3xl font-semibold tracking-tight text-text">
        {formatCount(value)}
      </p>
      <p className="font-display text-xs font-medium uppercase tracking-wider text-text-muted">
        {label}
      </p>

      {hasBreakdown && (
        <div className="flex flex-wrap gap-x-4 gap-y-1 pt-2 border-t border-border-muted mt-3">
          {Object.entries(breakdown).map(([connector, count]) => (
            <span
              key={connector}
              className="flex items-center gap-1.5 text-xs text-text-faint"
            >
              <ConnectorIcon name={connector} labelHidden />
              <span className="font-mono">{formatCount(count)}</span>
            </span>
          ))}
        </div>
      )}
    </article>
  );
}

/* ── Preference stat card ────────────────────────────────── */

const PREFERENCE_ORDER = ["star", "yah", "hmm", "nah"] as const;

function PreferenceStatCard({
  counts,
  delay,
}: {
  counts: Record<string, number>;
  delay?: string;
}) {
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  const hasAny = total > 0;

  return (
    <article
      className="animate-fade-up rounded-xl border border-border-muted bg-surface p-5 space-y-1"
      style={delay ? { animationDelay: delay } : undefined}
    >
      <span className="text-text-faint">
        <Sparkles className="size-5" />
      </span>
      <p className="font-mono text-3xl font-semibold tracking-tight text-text">
        {formatCount(total)}
      </p>
      <p className="font-display text-xs font-medium uppercase tracking-wider text-text-muted">
        Rated Tracks
      </p>

      {hasAny && (
        <div className="flex flex-wrap gap-x-4 gap-y-1 pt-2 border-t border-border-muted mt-3">
          {PREFERENCE_ORDER.map((state) => {
            const count = counts[state] ?? 0;
            if (count === 0) return null;
            return (
              <span
                key={state}
                className="flex items-center gap-1.5 text-xs text-text-faint"
              >
                <PreferenceBadge state={state} />
                <span className="font-mono">{formatCount(count)}</span>
              </span>
            );
          })}
        </div>
      )}
    </article>
  );
}

/* ── Stats grid ──────────────────────────────────────────── */

function StatsGrid({ stats }: { stats: DashboardStatsSchema }) {
  const connectorCount = Object.keys(stats.tracks_by_connector).length;
  const tracksLabel =
    connectorCount > 0
      ? `Tracks across ${pluralize(connectorCount, "service")}`
      : "Total Tracks";

  const linkedCount = Object.values(stats.playlists_by_connector ?? {}).reduce(
    (a, b) => a + b,
    0,
  );
  const playlistsLabel =
    linkedCount > 0 ? `Playlists \u00b7 ${linkedCount} linked` : "Playlists";

  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-5">
      <StatCard
        icon={<Music className="size-5" />}
        label={tracksLabel}
        value={stats.total_tracks}
        hero
        breakdown={stats.tracks_by_connector}
      />
      <StatCard
        icon={<Headphones className="size-5" />}
        label="Total Plays"
        value={stats.total_plays}
        breakdown={stats.plays_by_connector}
        delay="50ms"
      />
      <StatCard
        icon={<Heart className="size-5" />}
        label="Liked Tracks"
        value={stats.total_liked}
        breakdown={stats.liked_by_connector}
        delay="100ms"
      />
      <StatCard
        icon={<ListMusic className="size-5" />}
        label={playlistsLabel}
        value={stats.total_playlists}
        breakdown={stats.playlists_by_connector}
        delay="150ms"
      />
      <PreferenceStatCard counts={stats.preference_counts} delay="200ms" />
    </div>
  );
}

/* ── Page ─────────────────────────────────────────────────── */

export function Dashboard() {
  const { data, isLoading, isError, error } =
    useGetDashboardStatsApiV1StatsDashboardGet({
      query: { staleTime: STALE.STATIC },
    });
  const { data: matchingData, isLoading: matchingLoading } =
    useGetMatchingHealthApiV1StatsMatchingGet(undefined, {
      query: { staleTime: STALE.STATIC },
    });
  const { data: connectorsData } = useGetConnectorsApiV1ConnectorsGet({
    query: { staleTime: STALE.STATIC },
  });

  const stats = data?.status === 200 ? data.data : undefined;
  const health = matchingData?.status === 200 ? matchingData.data : undefined;
  const connectorLabels =
    connectorsData?.status === 200
      ? connectorsData.data
          .filter((c) => c.auth_method === "oauth")
          .map((c) => c.display_name)
      : [];

  return (
    <div className="space-y-8">
      <title>Dashboard — Mixd</title>
      <PageHeader
        title="Dashboard"
        description="Your music library at a glance"
      />

      <ScheduleFailuresBanner />

      <QueryStates
        loading={isLoading}
        isError={isError}
        error={error}
        errorHeading="Failed to load statistics"
        skeleton={
          <CardGridSkeleton
            count={4}
            gridClassName="sm:grid-cols-2 lg:grid-cols-4"
            bars={["size-5", "h-8 w-24", "h-3 w-16"]}
          />
        }
      >
        {stats &&
          (stats.total_tracks === 0 ? (
            <GettingStarted stats={stats} connectorLabels={connectorLabels} />
          ) : (
            <StatsGrid stats={stats} />
          ))}
      </QueryStates>

      {matchingLoading && <MatchingHealthSkeleton />}
      {health && <MatchingHealth health={health} />}
    </div>
  );
}
