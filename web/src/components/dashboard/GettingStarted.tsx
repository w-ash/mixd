import { Check, Compass, Link2, Upload } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router";
import type { DashboardStatsSchema } from "#/api/generated/model";
import { formatList } from "#/lib/format";
import { cn } from "#/lib/utils";

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

export function GettingStarted({
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
