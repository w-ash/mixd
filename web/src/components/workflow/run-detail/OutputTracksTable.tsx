import { ResponsiveTable } from "#/components/shared/ResponsiveTable";
import { SectionHeader } from "#/components/shared/SectionHeader";
import { TableCard } from "#/components/shared/TableCard";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "#/components/ui/table";
import { formatMetricHeader, formatMetricValue } from "#/lib/format";

/**
 * Card representation of an output track — used by ResponsiveTable below the
 * @2xl container threshold (typically iPhone / iPad portrait widths).
 */
function OutputTrackCard({
  track,
  rank,
  metricColumns,
}: {
  track: Record<string, unknown>;
  rank: number;
  metricColumns: string[];
}) {
  return (
    <TableCard
      leading={
        <span className="mt-0.5 shrink-0 font-mono text-xs tabular-nums text-text-faint">
          {rank}
        </span>
      }
    >
      <p className="truncate font-display text-sm font-medium text-text">
        {String(track.title ?? "")}
      </p>
      <p className="truncate text-sm text-text-muted">
        {String(track.artists ?? "")}
      </p>
      {metricColumns.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-xs">
          {metricColumns.map((col) => (
            <span key={col} className="inline-flex items-baseline gap-1">
              <span className="text-text-faint">{formatMetricHeader(col)}</span>
              <span className="font-mono tabular-nums text-text-muted">
                {formatMetricValue(
                  (track.metrics as Record<string, unknown>)?.[col],
                )}
              </span>
            </span>
          ))}
        </div>
      )}
    </TableCard>
  );
}

/** Output tracks table showing the final playlist result with dynamic metric columns. */
export function OutputTracksTable({
  tracks,
  metricColumns,
}: {
  tracks: Record<string, unknown>[];
  metricColumns: string[];
}) {
  if (tracks.length === 0) return null;

  return (
    <section className="mt-8 space-y-3">
      <SectionHeader title="Output Tracks" />
      <ResponsiveTable
        cards={
          <div className="flex flex-col gap-2">
            {tracks.map((track, i) => (
              <OutputTrackCard
                key={String(track.track_id ?? i)}
                track={track}
                rank={(track.rank as number) ?? i + 1}
                metricColumns={metricColumns}
              />
            ))}
          </div>
        }
        table={
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-12 text-right">#</TableHead>
                <TableHead>Title</TableHead>
                <TableHead>Artist</TableHead>
                {metricColumns.map((col) => (
                  <TableHead key={col} className="text-right">
                    {formatMetricHeader(col)}
                  </TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {tracks.map((track, i) => (
                <TableRow key={String(track.track_id ?? i)}>
                  <TableCell className="text-right font-mono text-xs tabular-nums text-text-faint">
                    {(track.rank as number) ?? i + 1}
                  </TableCell>
                  <TableCell className="font-medium text-text">
                    {String(track.title ?? "")}
                  </TableCell>
                  <TableCell className="text-text-muted">
                    {String(track.artists ?? "")}
                  </TableCell>
                  {metricColumns.map((col) => (
                    <TableCell
                      key={col}
                      className="text-right font-mono text-xs tabular-nums text-text-muted"
                    >
                      {formatMetricValue(
                        (track.metrics as Record<string, unknown>)?.[col],
                      )}
                    </TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        }
      />
    </section>
  );
}
