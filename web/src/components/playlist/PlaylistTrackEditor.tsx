import {
  closestCenter,
  DndContext,
  type DragEndEvent,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { useQueryClient } from "@tanstack/react-query";
import { GripVertical, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import type { PlaylistEntrySchema } from "#/api/generated/model";
import {
  getGetPlaylistApiV1PlaylistsPlaylistIdGetQueryKey,
  getGetPlaylistTracksApiV1PlaylistsPlaylistIdTracksGetQueryKey,
  getListPlaylistsApiV1PlaylistsGetQueryKey,
  useRemovePlaylistTracksApiV1PlaylistsPlaylistIdTracksDelete,
  useReorderPlaylistTracksApiV1PlaylistsPlaylistIdTracksReorderPatch,
} from "#/api/generated/playlists/playlists";
import { UnresolvedTag } from "#/components/shared/UnresolvedTag";
import { Button } from "#/components/ui/button";
import { Checkbox } from "#/components/ui/checkbox";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "#/components/ui/table";
import { useIsMobile } from "#/hooks/useIsMobile";
import { formatArtists, formatDate, formatDuration } from "#/lib/format";
import { pluralize } from "#/lib/pluralize";
import { toasts } from "#/lib/toasts";

// How long the "Removed — Undo" snackbar stays before the delete commits.
const UNDO_MS = 7000;
// Single-pending model: one removal snackbar id, reused across removals.
const REMOVAL_TOAST_ID = "playlist-track-removal";

// The tracks query caches the customFetch envelope, so the entries array lives
// two levels deep at `old.data.data`. These helpers read/replace it without
// reaching for `any`.
function readEntries(old: unknown): PlaylistEntrySchema[] | null {
  if (old && typeof old === "object" && "data" in old) {
    const env = old as { data?: { data?: PlaylistEntrySchema[] } };
    return env.data?.data ?? null;
  }
  return null;
}

function withEntries(old: unknown, next: PlaylistEntrySchema[]): unknown {
  if (old && typeof old === "object" && "data" in old) {
    const env = old as { data: Record<string, unknown> };
    return { ...env, data: { ...env.data, data: next } };
  }
  return old;
}

interface RowControls {
  index: number;
  selected: boolean;
  onToggleSelect: (id: string) => void;
  onRemove: (id: string) => void;
}

/** A draggable, selectable table row (desktop). */
function SortableTrackRow({
  entry,
  index,
  selected,
  onToggleSelect,
  onRemove,
}: { entry: PlaylistEntrySchema } & RowControls) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: entry.id });
  return (
    <TableRow
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      data-dragging={isDragging}
      className="data-[dragging=true]:relative data-[dragging=true]:z-10 data-[dragging=true]:bg-surface data-[dragging=true]:shadow-lg"
    >
      <TableCell className="w-20">
        <div className="flex items-center gap-1">
          <Checkbox
            checked={selected}
            onCheckedChange={() => onToggleSelect(entry.id)}
            aria-label={`Select ${entry.track.title}`}
          />
          <button
            type="button"
            className="cursor-grab touch-none rounded p-1 text-text-faint hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring active:cursor-grabbing"
            aria-label={`Reorder ${entry.track.title}`}
            {...attributes}
            {...listeners}
          >
            <GripVertical className="size-4" />
          </button>
        </div>
      </TableCell>
      <TableCell className="text-right tabular-nums text-text-muted">
        {index + 1}
      </TableCell>
      <TableCell>
        {entry.is_resolved === false ? (
          <div className="flex flex-col gap-0.5">
            <span className="font-medium text-text">{entry.track.title}</span>
            <UnresolvedTag />
          </div>
        ) : (
          <Link
            to={`/library/${entry.track.id}`}
            className="font-medium text-text transition-colors hover:text-primary"
          >
            {entry.track.title}
          </Link>
        )}
      </TableCell>
      <TableCell className="text-text-muted">
        {formatArtists(entry.track.artists)}
      </TableCell>
      <TableCell className="text-text-muted">
        {entry.track.album ?? "—"}
      </TableCell>
      <TableCell className="text-right tabular-nums text-text-muted">
        {formatDuration(entry.track.duration_ms)}
      </TableCell>
      <TableCell className="text-right text-sm text-text-muted">
        {formatDate(entry.added_at)}
      </TableCell>
      <TableCell className="w-10 text-right">
        <button
          type="button"
          onClick={() => onRemove(entry.id)}
          aria-label={`Remove ${entry.track.title}`}
          className="rounded p-1 text-text-faint transition-colors hover:text-destructive focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <X className="size-4" />
        </button>
      </TableCell>
    </TableRow>
  );
}

/** A draggable, selectable card (mobile). */
function SortableTrackCard({
  entry,
  selected,
  onToggleSelect,
  onRemove,
}: { entry: PlaylistEntrySchema } & RowControls) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: entry.id });
  return (
    <div
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      data-dragging={isDragging}
      className="flex items-start gap-2 rounded-md border border-border-muted bg-surface px-4 py-3 data-[dragging=true]:z-10 data-[dragging=true]:shadow-lg"
    >
      <Checkbox
        checked={selected}
        onCheckedChange={() => onToggleSelect(entry.id)}
        aria-label={`Select ${entry.track.title}`}
        className="mt-1"
      />
      <button
        type="button"
        className="mt-0.5 cursor-grab touch-none rounded p-1 text-text-faint hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring active:cursor-grabbing"
        aria-label={`Reorder ${entry.track.title}`}
        {...attributes}
        {...listeners}
      >
        <GripVertical className="size-4" />
      </button>
      <div className="min-w-0 flex-1">
        {entry.is_resolved === false ? (
          <div className="flex flex-col gap-0.5">
            <span className="font-medium text-text">{entry.track.title}</span>
            <UnresolvedTag />
          </div>
        ) : (
          <Link
            to={`/library/${entry.track.id}`}
            className="font-medium text-text"
          >
            {entry.track.title}
          </Link>
        )}
        <p className="truncate text-sm text-text-muted">
          {formatArtists(entry.track.artists)}
        </p>
        <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-text-muted">
          {entry.track.album && (
            <span className="truncate">{entry.track.album}</span>
          )}
          <span className="shrink-0 tabular-nums">
            {formatDuration(entry.track.duration_ms)}
          </span>
          {entry.added_at && (
            <span className="shrink-0">Added {formatDate(entry.added_at)}</span>
          )}
        </div>
      </div>
      <button
        type="button"
        onClick={() => onRemove(entry.id)}
        aria-label={`Remove ${entry.track.title}`}
        className="rounded p-1 text-text-faint transition-colors hover:text-destructive focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <X className="size-4" />
      </button>
    </div>
  );
}

/**
 * The editable track list: drag-and-drop / keyboard reorder, single + batch
 * remove with a deferred-commit "Undo" snackbar, and multi-select.
 *
 * All edits are optimistic against the tracks query cache (the page reads the
 * same cache, so it re-renders in lockstep). Reorder rolls back on API failure;
 * remove holds its DELETE for the undo window so an undone removal preserves the
 * entry's identity (id / added_at / position) — nothing reaches the server.
 */
export function PlaylistTrackEditor({
  playlistId,
  entries,
}: {
  playlistId: string;
  entries: PlaylistEntrySchema[];
}) {
  const isMobile = useIsMobile();
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const tracksKey =
    getGetPlaylistTracksApiV1PlaylistsPlaylistIdTracksGetQueryKey(playlistId);

  const pendingRef = useRef<{
    entryIds: string[];
    snapshot: PlaylistEntrySchema[];
  } | null>(null);

  const reorderMutation =
    useReorderPlaylistTracksApiV1PlaylistsPlaylistIdTracksReorderPatch({
      mutation: {
        onMutate: async ({ data }) => {
          await queryClient.cancelQueries({ queryKey: tracksKey });
          const previous = queryClient.getQueryData(tracksKey);
          const current = readEntries(previous) ?? [];
          const byId = new Map(current.map((e) => [e.id, e]));
          const next = data.entry_ids
            .map((id) => byId.get(id))
            .filter((e): e is PlaylistEntrySchema => e !== undefined);
          queryClient.setQueryData(tracksKey, (old) => withEntries(old, next));
          return { previous };
        },
        onError: (_err, _vars, context) => {
          const ctx = context as { previous?: unknown } | undefined;
          if (ctx?.previous !== undefined) {
            queryClient.setQueryData(tracksKey, ctx.previous);
          }
          toasts.message("Couldn't save the new order — order restored.");
        },
        onSettled: () => {
          queryClient.invalidateQueries({ queryKey: tracksKey });
        },
        meta: { suppressErrorToast: true },
      },
    });

  const removeMutation =
    useRemovePlaylistTracksApiV1PlaylistsPlaylistIdTracksDelete({
      mutation: {
        meta: { suppressErrorToast: true },
        // Invalidation lives at the useMutation level (not the mutate() call
        // site) so it fires even when the DELETE is committed after the
        // component unmounts — a `mutate()`-level onSettled is skipped once the
        // caller has unmounted, which would leave the count caches stale.
        onSettled: () => {
          queryClient.invalidateQueries({ queryKey: tracksKey });
          queryClient.invalidateQueries({
            queryKey:
              getGetPlaylistApiV1PlaylistsPlaylistIdGetQueryKey(playlistId),
          });
          queryClient.invalidateQueries({
            queryKey: getListPlaylistsApiV1PlaylistsGetQueryKey(),
          });
        },
      },
    });

  const sensors = useSensors(
    // Small activation distance so a click on the handle still registers as a
    // click (e.g. focus) rather than starting a drag immediately.
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    }),
  );

  // Returns the in-flight DELETE so callers (drag-reorder) can await it before
  // sending a dependent request. Rollback lives in our own try/catch rather than
  // a mutate()-level onError so it still runs if the commit fires after unmount;
  // invalidation is handled by removeMutation's useMutation-level onSettled.
  async function commitPendingRemoval() {
    const pending = pendingRef.current;
    if (!pending) return;
    pendingRef.current = null;
    try {
      await removeMutation.mutateAsync({
        playlistId,
        data: { entry_ids: pending.entryIds },
      });
    } catch {
      queryClient.setQueryData(tracksKey, (old) =>
        withEntries(old, pending.snapshot),
      );
      toasts.message("Couldn't remove — tracks restored.");
    }
  }

  function flushPendingRemoval() {
    if (pendingRef.current) {
      toasts.dismiss(REMOVAL_TOAST_ID);
      return commitPendingRemoval();
    }
  }

  function undoRemoval() {
    const pending = pendingRef.current;
    if (!pending) return;
    queryClient.setQueryData(tracksKey, (old) =>
      withEntries(old, pending.snapshot),
    );
    pendingRef.current = null;
  }

  function removeEntries(entryIds: string[]) {
    // Single-pending model: commit any in-flight removal before starting a new one.
    flushPendingRemoval();
    const previous = queryClient.getQueryData(tracksKey);
    const snapshot = readEntries(previous) ?? entries;
    const remaining = snapshot.filter((e) => !entryIds.includes(e.id));
    queryClient.setQueryData(tracksKey, (old) => withEntries(old, remaining));
    setSelected(new Set());
    pendingRef.current = { entryIds, snapshot };
    // Commit when the snackbar's countdown elapses. Driving the commit off the
    // toast lifecycle (not a parallel setTimeout) keeps the "Undo" affordance and
    // the irreversible DELETE on the SAME pause-aware deadline — sonner pauses
    // `duration` on hover/blur, so a raw timer could commit while Undo is still
    // visible. Undo and preempt go through dismiss (onDismiss), never onAutoClose.
    toasts.success(
      entryIds.length === 1
        ? "Removed track"
        : `Removed ${pluralize(entryIds.length, "track")}`,
      {
        id: REMOVAL_TOAST_ID,
        duration: UNDO_MS,
        action: { label: "Undo", onClick: undoRemoval },
        onAutoClose: () => {
          void commitPendingRemoval();
        },
      },
    );
  }

  async function handleDragEnd(event: DragEndEvent) {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    // Commit any pending removal first AND WAIT for it, so the backend's entry
    // set matches the full ordered list we're about to send (reorder is
    // exact-set; a still-in-flight DELETE would make the playlist longer than
    // our payload → 404). Reading the cache after the await reflects whichever
    // way the removal resolved (committed → rows gone; failed → rolled back).
    await flushPendingRemoval();
    const current = readEntries(queryClient.getQueryData(tracksKey)) ?? entries;
    const oldIndex = current.findIndex((e) => e.id === active.id);
    const newIndex = current.findIndex((e) => e.id === over.id);
    if (oldIndex < 0 || newIndex < 0) return;
    const next = arrayMove(current, oldIndex, newIndex);
    reorderMutation.mutate({
      playlistId,
      data: { entry_ids: next.map((e) => e.id) },
    });
  }

  // Commit a still-pending removal if the user navigates away. The ref keeps
  // the cleanup pointed at the latest flush without re-running it on every
  // render — an unkeyed effect cleanup fires before *every* re-render, which
  // would commit the delete immediately and defeat the undo window.
  const flushRef = useRef(flushPendingRemoval);
  flushRef.current = flushPendingRemoval;
  useEffect(
    () => () => {
      void flushRef.current();
    },
    [],
  );

  // Keep the selection in sync with the live entry set. A background refetch can
  // drop or swap entries; a stale id would inflate "N selected", falsely satisfy
  // "select all" (size match ≠ same ids), and — worst — ride along in the
  // all-or-nothing batch DELETE, where one unknown id 404s the entire removal.
  useEffect(() => {
    setSelected((prev) => {
      if (prev.size === 0) return prev;
      const live = new Set(entries.map((e) => e.id));
      let changed = false;
      const next = new Set<string>();
      for (const id of prev) {
        if (live.has(id)) next.add(id);
        else changed = true;
      }
      return changed ? next : prev;
    });
  }, [entries]);

  function toggleSelect(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const allSelected = entries.length > 0 && selected.size === entries.length;
  const someSelected = selected.size > 0 && !allSelected;
  function toggleSelectAll() {
    setSelected(allSelected ? new Set() : new Set(entries.map((e) => e.id)));
  }

  function titleOf(id: string | number): string {
    return entries.find((e) => e.id === String(id))?.track.title ?? "track";
  }
  function posOf(id: string | number): number {
    return entries.findIndex((e) => e.id === String(id)) + 1;
  }
  const announcements = {
    onDragStart: ({ active }: { active: { id: string | number } }) =>
      `Picked up ${titleOf(active.id)}. It is in position ${posOf(active.id)} of ${entries.length}.`,
    onDragOver: ({
      over,
      active,
    }: {
      active: { id: string | number };
      over: { id: string | number } | null;
    }) =>
      over
        ? `${titleOf(active.id)} moved to position ${posOf(over.id)} of ${entries.length}.`
        : "",
    onDragEnd: ({
      over,
      active,
    }: {
      active: { id: string | number };
      over: { id: string | number } | null;
    }) =>
      over
        ? `${titleOf(active.id)} dropped at position ${posOf(over.id)} of ${entries.length}.`
        : "Reorder cancelled.",
    onDragCancel: ({ active }: { active: { id: string | number } }) =>
      `Reorder cancelled. ${titleOf(active.id)} returned to its position.`,
  };

  const ids = entries.map((e) => e.id);
  const rowControls = (entry: PlaylistEntrySchema, index: number) => ({
    index,
    selected: selected.has(entry.id),
    onToggleSelect: toggleSelect,
    onRemove: (id: string) => removeEntries([id]),
  });

  return (
    <DndContext
      sensors={sensors}
      collisionDetection={closestCenter}
      onDragEnd={handleDragEnd}
      accessibility={{ announcements }}
    >
      {selected.size > 0 && (
        <section
          aria-label="Bulk selection"
          className="mb-3 flex items-center gap-3 rounded-md border border-primary/40 bg-primary/5 px-3 py-2 text-sm"
        >
          <span className="font-display text-text">
            {selected.size} selected
          </span>
          <Button
            size="sm"
            variant="destructive"
            onClick={() => removeEntries(Array.from(selected))}
          >
            Remove selected
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setSelected(new Set())}
            aria-label="Clear selection"
          >
            <X className="mr-1 size-3.5" />
            Clear
          </Button>
        </section>
      )}

      {isMobile ? (
        <SortableContext items={ids} strategy={verticalListSortingStrategy}>
          <div className="flex flex-col gap-2">
            {entries.map((entry, index) => (
              <SortableTrackCard
                key={entry.id}
                entry={entry}
                {...rowControls(entry, index)}
              />
            ))}
          </div>
        </SortableContext>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-20">
                <Checkbox
                  checked={
                    allSelected ? true : someSelected ? "indeterminate" : false
                  }
                  onCheckedChange={toggleSelectAll}
                  aria-label="Select all tracks"
                />
              </TableHead>
              <TableHead className="w-12 text-right">#</TableHead>
              <TableHead>Title</TableHead>
              <TableHead>Artists</TableHead>
              <TableHead>Album</TableHead>
              <TableHead className="w-20 text-right">Duration</TableHead>
              <TableHead className="w-32 text-right">Added</TableHead>
              <TableHead className="w-10" />
            </TableRow>
          </TableHeader>
          <SortableContext items={ids} strategy={verticalListSortingStrategy}>
            <TableBody>
              {entries.map((entry, index) => (
                <SortableTrackRow
                  key={entry.id}
                  entry={entry}
                  {...rowControls(entry, index)}
                />
              ))}
            </TableBody>
          </SortableContext>
        </Table>
      )}
    </DndContext>
  );
}
