import { AlertTriangle, Check, Clock, Loader2, X } from "lucide-react";

import type { ImportQueueEntrySchema } from "#/api/generated/model";
import { Button } from "#/components/ui/button";
import type { SubOperationProgress } from "#/hooks/useOperationProgress";
import { formatCount, formatTotalDuration } from "#/lib/format";
import {
  isSettled,
  orderedEntries,
  placementOf,
  summarizeQueue,
} from "#/lib/import-queue";
import { pluralize } from "#/lib/pluralize";
import { cn } from "#/lib/utils";

/** The pipeline every file goes through. Keys are the shared `Phase`
 * vocabulary the backend tags operations with. */
const PHASES: ReadonlyArray<{ key: string; label: string }> = [
  { key: "fetch", label: "Ingest" },
  { key: "match", label: "Resolve" },
  { key: "save", label: "Project" },
];

interface ImportQueueManifestProps {
  entries: ImportQueueEntrySchema[];
  /** Live sub-operation from the drain's stream. Null when unattached — rows
   * render from the queue either way; the stream only sharpens the running one. */
  subOperation: SubOperationProgress | null;
  onCancelRemaining: () => void;
  cancelDisabled: boolean;
}

export function ImportQueueManifest({
  entries,
  subOperation,
  onCancelRemaining,
  cancelDisabled,
}: ImportQueueManifestProps) {
  const summary = summarizeQueue(entries);
  const drained = summary.queued === 0 && summary.running === 0;

  return (
    <section className="mt-3" aria-label="Import queue">
      <ManifestHeader summary={summary} drained={drained} />

      <ul className="mt-3 divide-y divide-border rounded-lg border border-border">
        {orderedEntries(entries).map((entry) => (
          <li key={entry.position}>
            <EntryRow
              entry={entry}
              entries={entries}
              subOperation={entry.status === "running" ? subOperation : null}
            />
          </li>
        ))}
      </ul>

      {summary.queued > 0 && (
        <div className="mt-3 flex justify-end">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            disabled={cancelDisabled}
            onClick={onCancelRemaining}
          >
            Cancel {pluralize(summary.queued, "remaining file")}
          </Button>
        </div>
      )}
    </section>
  );
}

function ManifestHeader({
  summary,
  drained,
}: {
  summary: ReturnType<typeof summarizeQueue>;
  drained: boolean;
}) {
  const imported = summary.succeeded + summary.partial;
  const headline = drained
    ? `Imported ${imported} of ${pluralize(summary.total, "file")}`
    : `Importing ${Math.min(summary.settled + 1, summary.total)} of ${summary.total} files`;

  const detail = [
    summary.succeeded + summary.partial > 0 &&
      `${summary.succeeded + summary.partial} done`,
    summary.failed > 0 && `${summary.failed} failed`,
    summary.cancelled > 0 && `${summary.cancelled} cancelled`,
    summary.queued > 0 && `${summary.queued} queued`,
  ].filter((part): part is string => typeof part === "string");

  return (
    <output className="block space-y-2" aria-live="polite">
      <div className="flex items-center justify-between gap-3">
        <span className="font-display text-sm text-text">{headline}</span>
        <span className="shrink-0 font-mono text-xs tabular-nums text-text-muted">
          {summary.etaSeconds !== null && !drained
            ? `~${formatTotalDuration(summary.etaSeconds * 1000)} left`
            : summary.plays > 0
              ? `${formatCount(summary.plays)} plays`
              : null}
        </span>
      </div>

      <div className="h-1.5 w-full overflow-hidden rounded-full bg-surface-elevated">
        <div
          className={cn(
            "h-full rounded-full transition-all duration-300 ease-out",
            // Neutral while running: a green or red bar six minutes into
            // thirty is a verdict on work that has not happened yet.
            !drained
              ? "bg-primary"
              : summary.failed > 0
                ? "bg-status-expired"
                : "bg-status-connected",
          )}
          style={{ width: `${Math.max(summary.percent, 2)}%` }}
        />
      </div>

      {detail.length > 0 && (
        <p className="text-xs text-text-muted">{detail.join(" · ")}</p>
      )}
    </output>
  );
}

function EntryRow({
  entry,
  entries,
  subOperation,
}: {
  entry: ImportQueueEntrySchema;
  entries: ImportQueueEntrySchema[];
  subOperation: SubOperationProgress | null;
}) {
  const running = entry.status === "running";
  return (
    <div className={cn("px-4 py-3", running && "bg-surface-elevated/40")}>
      {/* Stacked on narrow widths: these filenames are long and near-identical,
          so sharing a line truncates away the part that tells rows apart. */}
      <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:gap-3">
        <div className="flex min-w-0 items-center gap-2">
          <EntryIcon status={entry.status} />
          <span className="truncate font-mono text-xs text-text">
            {entry.filename}
          </span>
        </div>
        <span className="shrink-0 pl-5 text-xs text-text-muted sm:ml-auto sm:pl-0">
          <EntryDetail entry={entry} entries={entries} />
        </span>
      </div>

      {running && <RunningDetail subOperation={subOperation} />}
    </div>
  );
}

function EntryIcon({ status }: { status: string }) {
  const shared = "size-3 shrink-0";
  if (status === "running")
    return (
      <Loader2
        className={cn(shared, "animate-spin text-primary")}
        aria-label="Running"
      />
    );
  if (status === "complete")
    return (
      <Check
        className={cn(shared, "text-status-connected")}
        aria-label="Complete"
      />
    );
  if (status === "partial")
    return (
      <AlertTriangle
        className={cn(shared, "text-status-expired")}
        aria-label="Completed with issues"
      />
    );
  if (status === "error")
    return (
      <AlertTriangle
        className={cn(shared, "text-destructive")}
        aria-label="Failed"
      />
    );
  if (status === "cancelled")
    return (
      <X className={cn(shared, "text-text-faint")} aria-label="Cancelled" />
    );
  return (
    <Clock className={cn(shared, "text-text-faint")} aria-label="Queued" />
  );
}

/** The trailing half of a row: what this file is waiting on, or what it did. */
function EntryDetail({
  entry,
  entries,
}: {
  entry: ImportQueueEntrySchema;
  entries: ImportQueueEntrySchema[];
}) {
  if (entry.status === "queued") {
    const placement = placementOf(entries, entry);
    if (placement === null) return <>Queued</>;
    return (
      <>
        Queued · #{placement.place}
        {placement.startsInSeconds !== null &&
          ` · starts in ~${formatTotalDuration(placement.startsInSeconds * 1000)}`}
      </>
    );
  }
  if (entry.status === "cancelled") return <>Cancelled</>;
  if (entry.status === "error") {
    const reason = entry.counts?.error_message;
    return <>Failed{typeof reason === "string" ? ` — ${reason}` : ""}</>;
  }
  if (!isSettled(entry)) return <>{elapsedLabel(entry)}</>;

  const plays = entry.counts?.track_plays;
  const parts = [
    typeof plays === "number" ? `${formatCount(plays)} plays` : null,
    elapsedLabel(entry),
  ].filter((part): part is string => part !== null);
  return <span className="font-mono tabular-nums">{parts.join(" · ")}</span>;
}

function elapsedLabel(entry: ImportQueueEntrySchema): string {
  if (!entry.started_at) return "";
  const end = entry.settled_at ? new Date(entry.settled_at) : new Date();
  return formatTotalDuration(
    end.getTime() - new Date(entry.started_at).getTime(),
  );
}

/**
 * The one expanded row — a serial queue never has two files running, so the
 * expensive detail is affordable exactly once.
 */
function RunningDetail({
  subOperation,
}: {
  subOperation: SubOperationProgress | null;
}) {
  // Three empty steps read as "none of this has started" when in truth only
  // the live channel is missing; the row's spinner already says it is running.
  if (subOperation === null) return null;

  const activeIndex = PHASES.findIndex(
    (phase) => phase.key === subOperation.phase,
  );
  const determinate =
    subOperation.total != null && subOperation.completionPercentage != null;

  return (
    <div className="mt-2 space-y-1.5 pl-5">
      <ol className="flex items-center gap-2 text-xs">
        {PHASES.map((phase, index) => {
          const done = activeIndex > index;
          const active = activeIndex === index;
          return (
            <li key={phase.key} className="flex items-center gap-2">
              {index > 0 && (
                <span className="h-px w-3 bg-border" aria-hidden="true" />
              )}
              <span
                className={cn(
                  "flex items-center gap-1",
                  done && "text-status-connected",
                  active && "text-primary",
                  !done && !active && "text-text-faint",
                )}
              >
                <span
                  className={cn(
                    "size-1.5 rounded-full",
                    done && "bg-status-connected",
                    active && "bg-primary",
                    !done && !active && "border border-current",
                  )}
                  aria-hidden="true"
                />
                {phase.label}
              </span>
            </li>
          );
        })}
      </ol>

      <div className="h-1 w-full overflow-hidden rounded-full bg-surface-elevated">
        <div
          className={cn(
            "h-full rounded-full bg-primary/60 transition-all duration-300 ease-out",
            // Indeterminate until the phase knows its own total.
            !determinate && "w-full animate-pulse opacity-40",
          )}
          style={
            determinate
              ? {
                  width: `${Math.max(subOperation.completionPercentage ?? 0, 2)}%`,
                }
              : undefined
          }
        />
      </div>
      <p className="truncate text-xs text-text-muted">{subOperation.message}</p>
    </div>
  );
}
