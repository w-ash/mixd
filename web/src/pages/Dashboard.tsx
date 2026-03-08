import {
  AlertTriangle,
  Headphones,
  Heart,
  ListMusic,
  Music,
} from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router";
import type { DashboardStatsSchema } from "@/api/generated/model";
import { useGetDashboardStatsApiV1StatsDashboardGet } from "@/api/generated/stats/stats";
import { PageHeader } from "@/components/layout/PageHeader";
import { ConnectorIcon } from "@/components/shared/ConnectorIcon";
import { EmptyState } from "@/components/shared/EmptyState";
import { Skeleton } from "@/components/ui/skeleton";
import { formatCount } from "@/lib/format";
import { cn } from "@/lib/utils";

/* ── Skeleton loader ─────────────────────────────────────── */

function DashboardSkeleton() {
  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      {Array.from({ length: 4 }).map((_, i) => (
        <div
          // biome-ignore lint/suspicious/noArrayIndexKey: static skeleton placeholders
          key={i}
          className="rounded-xl border border-border-muted bg-surface p-5 space-y-3"
        >
          <Skeleton className="size-5" />
          <Skeleton className="h-8 w-24" />
          <Skeleton className="h-3 w-16" />
        </div>
      ))}
    </div>
  );
}

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
              <ConnectorIcon name={connector} labelHidden iconSize="sm" />
              <span className="font-mono">{formatCount(count)}</span>
            </span>
          ))}
        </div>
      )}
    </article>
  );
}

/* ── Stats grid ──────────────────────────────────────────── */

function StatsGrid({ stats }: { stats: DashboardStatsSchema }) {
  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <StatCard
        icon={<Music className="size-5" />}
        label="Total Tracks"
        value={stats.total_tracks}
        hero
        breakdown={stats.tracks_by_connector}
      />
      <StatCard
        icon={<Headphones className="size-5" />}
        label="Total Plays"
        value={stats.total_plays}
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
        label="Playlists"
        value={stats.total_playlists}
        delay="150ms"
      />
    </div>
  );
}

/* ── Page ─────────────────────────────────────────────────── */

export function Dashboard() {
  const { data, isLoading, isError, error } =
    useGetDashboardStatsApiV1StatsDashboardGet();

  const stats = data?.status === 200 ? data.data : undefined;

  return (
    <div>
      <title>Dashboard — Narada</title>
      <PageHeader
        title="Dashboard"
        description="Your music library at a glance"
      />

      {isLoading && <DashboardSkeleton />}

      {isError && (
        <EmptyState
          icon={<AlertTriangle className="size-10" />}
          heading="Failed to load statistics"
          description={
            error instanceof Error
              ? error.message
              : "An unexpected error occurred."
          }
        />
      )}

      {!isLoading &&
        !isError &&
        stats &&
        (stats.total_tracks === 0 ? (
          <EmptyState
            icon={<Music className="size-10" />}
            heading="No data yet"
            description="Connect services in Settings to get started."
            action={
              <Link
                to="/settings"
                className="inline-flex items-center gap-1.5 rounded-md bg-primary px-4 py-2 font-display text-sm font-medium text-surface transition-colors hover:bg-primary/90"
              >
                Go to Settings
              </Link>
            }
          />
        ) : (
          <StatsGrid stats={stats} />
        ))}
    </div>
  );
}
