import {
  Bar,
  BarChart,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
} from "recharts";
import type { HistogramBinSchema } from "#/api/generated/model";
import type { PlayHistogramResponseBucket } from "#/api/generated/model/playHistogramResponseBucket";

interface PlaysBarChartProps {
  bins: HistogramBinSchema[];
  bucket: PlayHistogramResponseBucket;
  /** Bar click — receives the bin's start timestamp. Makes the chart a date navigator. */
  onBarClick?: (binStart: string) => void;
  /** Fixed pixel size for tests — jsdom gives ResponsiveContainer zero width. */
  width?: number;
  height?: number;
}

function formatBinLabel(
  iso: string,
  bucket: PlayHistogramResponseBucket,
): string {
  const d = new Date(iso);
  if (bucket === "month") {
    return d.toLocaleDateString(undefined, { month: "short", year: "2-digit" });
  }
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

interface TooltipEntry {
  payload?: { bucket_start: string; count: number };
}

function ChartTooltip({
  active,
  payload,
  bucket,
}: {
  active?: boolean;
  payload?: TooltipEntry[];
  bucket: PlayHistogramResponseBucket;
}) {
  const bin = payload?.[0]?.payload;
  if (!active || !bin) return null;
  return (
    <div className="rounded-md border border-border bg-surface-elevated px-3 py-2 shadow-md">
      <p className="font-display text-xs text-text-muted">
        {formatBinLabel(bin.bucket_start, bucket)}
      </p>
      <p className="font-mono text-sm tabular-nums text-text">
        {bin.count} play{bin.count === 1 ? "" : "s"}
      </p>
    </div>
  );
}

/**
 * Plays-over-time bar navigator. Single gold series — no legend; recessive
 * axis; clicking a bar narrows the feed to that period.
 */
export function PlaysBarChart({
  bins,
  bucket,
  onBarClick,
  width,
  height = 160,
}: PlaysBarChartProps) {
  if (bins.length === 0) return null;

  const chart = (
    <BarChart
      data={bins}
      width={width}
      height={width ? height : undefined}
      margin={{ top: 4, right: 0, bottom: 0, left: 0 }}
      barCategoryGap="15%"
    >
      <XAxis
        dataKey="bucket_start"
        tickFormatter={(v: string) => formatBinLabel(v, bucket)}
        interval="preserveStartEnd"
        minTickGap={48}
        tickLine={false}
        axisLine={{ stroke: "var(--color-border-muted)" }}
        tick={{ fill: "var(--color-text-faint)", fontSize: 11 }}
      />
      <Tooltip
        cursor={{ fill: "var(--color-surface-elevated)" }}
        content={<ChartTooltip bucket={bucket} />}
      />
      <Bar
        dataKey="count"
        fill="var(--color-chart-1)"
        radius={[4, 4, 0, 0]}
        maxBarSize={24}
        className={onBarClick ? "cursor-pointer" : undefined}
        onClick={(item) => {
          const bin = (item as { payload?: HistogramBinSchema }).payload;
          if (bin?.bucket_start) onBarClick?.(bin.bucket_start);
        }}
      >
        {bins.map((bin) => (
          <Cell key={bin.bucket_start} />
        ))}
      </Bar>
    </BarChart>
  );

  if (width) return chart;
  return (
    <div className="h-40 w-full" data-testid="plays-bar-chart">
      <ResponsiveContainer width="100%" height="100%">
        {chart}
      </ResponsiveContainer>
    </div>
  );
}
