import type { WorkflowRunNodeSchema } from "#/api/generated/model";
import { pluralize } from "#/lib/pluralize";
import { cn } from "#/lib/utils";
import type {
  PlaylistChanges,
  PlaylistChangeTrack,
} from "#/lib/workflow-config";

/** Expandable panel showing playlist changes for destination nodes. */
export function PlaylistChangesPanel({
  node,
}: {
  node: WorkflowRunNodeSchema;
}) {
  const changes = node.node_details?.playlist_changes as
    | PlaylistChanges
    | undefined;
  if (!changes) return null;

  return (
    <div className="mt-3 space-y-3">
      {changes.tracks_removed.length > 0 && (
        <TrackChangeGroup
          label="Removed from playlist"
          tracks={changes.tracks_removed}
          total={changes.tracks_removed_total}
          className="text-destructive/80"
        />
      )}
      {changes.tracks_added.length > 0 && (
        <TrackChangeGroup
          label="Added to playlist"
          tracks={changes.tracks_added}
          total={changes.tracks_added_total}
          className="text-status-connected/80"
        />
      )}
      {changes.tracks_moved > 0 && (
        <p className="px-2 text-xs text-text-muted">
          {pluralize(changes.tracks_moved, "track")} reordered
        </p>
      )}
    </div>
  );
}

function TrackChangeGroup({
  label,
  tracks,
  total,
  className,
}: {
  label: string;
  tracks: PlaylistChangeTrack[];
  total?: number;
  className?: string;
}) {
  const actualTotal = total ?? tracks.length;
  const remaining = actualTotal - tracks.length;

  return (
    <div>
      <p className={cn("mb-1 font-display text-xs font-medium", className)}>
        {label} ({actualTotal})
      </p>
      <div className="space-y-px">
        {tracks.map((t) => (
          <div
            key={t.track_id}
            className="flex items-baseline gap-3 rounded px-2 py-1 text-xs hover:bg-surface-sunken/50"
          >
            <span className="min-w-0 truncate text-text">{t.title}</span>
            <span className="shrink-0 text-text-faint">{t.artists}</span>
          </div>
        ))}
        {remaining > 0 && (
          <p className="px-2 py-1 text-xs text-text-muted">
            and {remaining} more
          </p>
        )}
      </div>
    </div>
  );
}
