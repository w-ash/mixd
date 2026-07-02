import type {
  MatchMethodHealthSchema,
  MethodHealthStatSchema,
} from "#/api/generated/model";
import { ConnectorIcon } from "#/components/shared/ConnectorIcon";
import { ResponsiveTable } from "#/components/shared/ResponsiveTable";
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
import { formatCount } from "#/lib/format";
import { cn } from "#/lib/utils";

const CATEGORY_ORDER = [
  "Primary Import",
  "Identity Resolution",
  "Cross-Service Discovery",
  "Error Recovery",
  "Secondary Cache",
] as const;

export function MatchingHealthSkeleton() {
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

export function MatchingHealth({
  health,
}: {
  health: MatchMethodHealthSchema;
}) {
  if (health.stats.length === 0) return null;

  const grouped = groupByCategory(health.stats);

  return (
    <section className="space-y-5">
      <SectionHeader
        title="Match Method Health"
        description={`${formatCount(health.total_mappings)} total mappings · last ${health.recent_days} days`}
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

              <ResponsiveTable
                cards={
                  <div className="flex flex-col gap-2 p-3">
                    {methods.map((method) => {
                      const variant = confidenceVariant(method.avg_confidence);
                      return (
                        <article
                          key={`${method.match_method}-${method.connector_name}-card`}
                          className="flex items-start gap-3 rounded-md border border-border bg-surface px-3 py-2"
                        >
                          <ConnectorIcon
                            name={method.connector_name}
                            labelHidden
                          />
                          <div className="min-w-0 flex-1">
                            <p
                              className="truncate font-mono text-xs text-text"
                              title={method.description}
                            >
                              {method.match_method}
                            </p>
                            <div className="mt-1 flex flex-wrap items-baseline gap-x-3 gap-y-0.5 font-mono text-[11px] text-text-muted">
                              <span>
                                Total {formatCount(method.total_count)}
                              </span>
                              <span>
                                {health.recent_days}d{" "}
                                {formatCount(method.recent_count)}
                              </span>
                              <span className={cn(variantColorClass[variant])}>
                                Conf {method.avg_confidence.toFixed(1)}
                              </span>
                            </div>
                          </div>
                        </article>
                      );
                    })}
                  </div>
                }
                table={
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
                        const variant = confidenceVariant(
                          method.avg_confidence,
                        );

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
                }
              />
            </article>
          );
        })}
      </div>
    </section>
  );
}
