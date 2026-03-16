import {
  AlertTriangle,
  Headphones,
  Heart,
  ListMusic,
  Music,
} from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router";
import type {
  DashboardStatsSchema,
  MatchMethodHealthSchema,
  MethodHealthStatSchema,
} from "@/api/generated/model";
import {
  useGetDashboardStatsApiV1StatsDashboardGet,
  useGetMatchingHealthApiV1StatsMatchingGet,
} from "@/api/generated/stats/stats";
import { PageHeader } from "@/components/layout/PageHeader";
import { ConnectorIcon } from "@/components/shared/ConnectorIcon";
import { EmptyState } from "@/components/shared/EmptyState";
import { SectionHeader } from "@/components/shared/SectionHeader";
import {
  confidenceVariant,
  variantColorClass,
} from "@/components/shared/StatusIndicator";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
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
  const connectorCount = Object.keys(stats.tracks_by_connector).length;
  const tracksLabel =
    connectorCount > 0
      ? `Tracks across ${connectorCount} ${connectorCount === 1 ? "service" : "services"}`
      : "Total Tracks";

  const linkedCount = Object.values(stats.playlists_by_connector ?? {}).reduce(
    (a, b) => a + b,
    0,
  );
  const playlistsLabel =
    linkedCount > 0 ? `Playlists \u00b7 ${linkedCount} linked` : "Playlists";

  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
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
    </div>
  );
}

/* ── Match method health ──────────────────────────────────── */

const CATEGORY_ORDER = [
  "Primary Import",
  "Identity Resolution",
  "Cross-Service Discovery",
  "Error Recovery",
  "Secondary Cache",
] as const;

function MatchingHealthSkeleton() {
  return (
    <div className="space-y-4">
      <Skeleton className="h-4 w-48" />
      <Skeleton className="h-3 w-32" />
      {Array.from({ length: 2 }).map((_, i) => (
        <div
          // biome-ignore lint/suspicious/noArrayIndexKey: static skeleton placeholders
          key={i}
          className="rounded-xl border border-border-muted bg-surface p-4 space-y-3"
        >
          <Skeleton className="h-4 w-40" />
          <Skeleton className="h-20 w-full" />
        </div>
      ))}
    </div>
  );
}

function groupByCategory(
  stats: MethodHealthStatSchema[],
): Map<string, MethodHealthStatSchema[]> {
  const groups = new Map<string, MethodHealthStatSchema[]>();
  for (const stat of stats) {
    const existing = groups.get(stat.category);
    if (existing) {
      existing.push(stat);
    } else {
      groups.set(stat.category, [stat]);
    }
  }
  return groups;
}

function MatchingHealth({ health }: { health: MatchMethodHealthSchema }) {
  if (health.stats.length === 0) return null;

  const grouped = groupByCategory(health.stats);

  return (
    <section className="space-y-5">
      <SectionHeader
        title="Match Method Health"
        description={`${formatCount(health.total_mappings)} total mappings \u00b7 last ${health.recent_days} days`}
      />

      <div className="grid gap-4 lg:grid-cols-2">
        {CATEGORY_ORDER.flatMap((cat) => {
          const methods = grouped.get(cat);
          return methods ? [{ category: cat, methods }] : [];
        }).map(({ category, methods }, i) => {
          const categoryTotal = methods.reduce(
            (sum, m) => sum + m.total_count,
            0,
          );

          return (
            <article
              key={category}
              className="animate-fade-up rounded-xl border border-border-muted bg-surface"
              style={{ animationDelay: `${(i + 1) * 75}ms` }}
            >
              <div className="flex items-baseline gap-2 border-b border-border-muted px-4 py-3">
                <h3 className="font-display text-xs font-medium uppercase tracking-wider text-text-muted">
                  {category}
                </h3>
                <span className="font-mono text-xs text-text-faint">
                  {formatCount(categoryTotal)}
                </span>
              </div>

              <Table>
                <TableHeader>
                  <TableRow className="hover:bg-transparent">
                    <TableHead>Method</TableHead>
                    <TableHead>Service</TableHead>
                    <TableHead className="text-right">Total</TableHead>
                    <TableHead className="text-right">
                      {health.recent_days}d
                    </TableHead>
                    <TableHead className="text-right">Confidence</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {methods.map((method) => {
                    const variant = confidenceVariant(method.avg_confidence);

                    return (
                      <TableRow
                        key={`${method.match_method}-${method.connector_name}`}
                      >
                        <TableCell
                          className="font-mono text-xs text-text"
                          title={method.description}
                        >
                          {method.match_method}
                        </TableCell>
                        <TableCell>
                          <ConnectorIcon
                            name={method.connector_name}
                            labelHidden
                            iconSize="sm"
                          />
                        </TableCell>
                        <TableCell className="text-right font-mono text-xs text-text-muted">
                          {formatCount(method.total_count)}
                        </TableCell>
                        <TableCell className="text-right font-mono text-xs text-text-muted">
                          {formatCount(method.recent_count)}
                        </TableCell>
                        <TableCell className="text-right">
                          <span
                            className={cn(
                              "font-mono text-xs",
                              variantColorClass[variant],
                            )}
                          >
                            {method.avg_confidence.toFixed(1)}
                          </span>
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </article>
          );
        })}
      </div>
    </section>
  );
}

/* ── Page ─────────────────────────────────────────────────── */

export function Dashboard() {
  const { data, isLoading, isError, error } =
    useGetDashboardStatsApiV1StatsDashboardGet();
  const { data: matchingData, isLoading: matchingLoading } =
    useGetMatchingHealthApiV1StatsMatchingGet();

  const stats = data?.status === 200 ? data.data : undefined;
  const health = matchingData?.status === 200 ? matchingData.data : undefined;

  return (
    <div className="space-y-10">
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
              <Button size="sm" asChild>
                <Link to="/settings/integrations">Go to Settings</Link>
              </Button>
            }
          />
        ) : (
          <StatsGrid stats={stats} />
        ))}

      {matchingLoading && <MatchingHealthSkeleton />}
      {health && <MatchingHealth health={health} />}
    </div>
  );
}
