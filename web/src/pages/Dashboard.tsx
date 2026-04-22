import {
  Check,
  Compass,
  Headphones,
  Heart,
  Link2,
  ListMusic,
  Music,
  Sparkles,
  Upload,
} from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router";
import { useGetConnectorsApiV1ConnectorsGet } from "#/api/generated/connectors/connectors";
import type {
  DashboardStatsSchema,
  MatchMethodHealthSchema,
  MethodHealthStatSchema,
} from "#/api/generated/model";
import {
  useGetDashboardStatsApiV1StatsDashboardGet,
  useGetMatchingHealthApiV1StatsMatchingGet,
} from "#/api/generated/stats/stats";
import { STALE } from "#/api/query-client";
import { PageHeader } from "#/components/layout/PageHeader";
import { ConnectorIcon } from "#/components/shared/ConnectorIcon";
import { PreferenceBadge } from "#/components/shared/PreferenceToggle";
import { QueryErrorState } from "#/components/shared/QueryErrorState";
import { SectionHeader } from "#/components/shared/SectionHeader";
import {
  confidenceVariant,
  variantColorClass,
} from "#/components/shared/StatusIndicator";
import { Skeleton } from "#/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "#/components/ui/table";
import { formatCount, formatList } from "#/lib/format";
import { pluralize } from "#/lib/pluralize";
import { cn } from "#/lib/utils";

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
          className="rounded-xl border border-border-muted bg-surface p-5 space-y-3"
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

/* ── Getting-started checklist ────────────────────────────── */

interface StepCardProps {
  step: number;
  title: string;
  description: string;
  to: string;
  done: boolean;
  icon: ReactNode;
}

function StepCard({ step, title, description, to, done, icon }: StepCardProps) {
  return (
    <Link
      to={to}
      className={cn(
        "group flex items-start gap-4 rounded-xl border p-5 transition-all duration-150",
        done
          ? "border-border-muted bg-surface opacity-60"
          : "border-border bg-surface-elevated shadow-elevated hover:shadow-glow hover:border-primary/20",
      )}
    >
      <span
        className={cn(
          "flex size-9 shrink-0 items-center justify-center rounded-full text-sm font-semibold",
          done
            ? "bg-status-liked/15 text-status-liked"
            : "bg-primary/10 text-primary",
        )}
      >
        {done ? <Check className="size-4" /> : step}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="text-text-faint">{icon}</span>
          <h3 className="font-display text-sm font-semibold text-text">
            {title}
          </h3>
        </div>
        <p className="mt-0.5 text-sm text-text-muted">{description}</p>
      </div>
    </Link>
  );
}

function GettingStarted({
  stats,
  connectorLabels,
}: {
  stats: DashboardStatsSchema;
  connectorLabels: string[];
}) {
  const hasConnectors = Object.keys(stats.tracks_by_connector).length > 0;
  const hasData = stats.total_tracks > 0 || stats.total_plays > 0;
  const connectDescription =
    connectorLabels.length === 0
      ? "Link your music services."
      : `Link your ${formatList(connectorLabels)} ${connectorLabels.length === 1 ? "account" : "accounts"}.`;

  return (
    <div className="mx-auto max-w-lg space-y-4">
      <div className="text-center space-y-1">
        <h2 className="font-display text-lg font-medium text-text">
          Welcome to Mixd
        </h2>
        <p className="text-sm text-text-muted">
          Get started by connecting your music services and importing your data.
        </p>
      </div>

      <div className="space-y-3">
        <StepCard
          step={1}
          icon={<Link2 className="size-4" />}
          title="Connect services"
          description={connectDescription}
          to="/settings/integrations"
          done={hasConnectors}
        />
        <StepCard
          step={2}
          icon={<Upload className="size-4" />}
          title="Import your music"
          description="Sync liked tracks and listening history."
          to="/settings/sync"
          done={hasData}
        />
        <StepCard
          step={3}
          icon={<Compass className="size-4" />}
          title="Explore your library"
          description="Browse tracks, playlists, and play stats."
          to="/library"
          done={false}
        />
      </div>
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

      {isLoading && <DashboardSkeleton />}

      {isError && (
        <QueryErrorState error={error} heading="Failed to load statistics" />
      )}

      {!isLoading &&
        !isError &&
        stats &&
        (stats.total_tracks === 0 ? (
          <GettingStarted stats={stats} connectorLabels={connectorLabels} />
        ) : (
          <StatsGrid stats={stats} />
        ))}

      {matchingLoading && <MatchingHealthSkeleton />}
      {health && <MatchingHealth health={health} />}
    </div>
  );
}
