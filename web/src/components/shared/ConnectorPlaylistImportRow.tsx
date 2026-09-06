import { useMutation } from "@tanstack/react-query";
import { MoreHorizontal } from "lucide-react";

import type {
  ActiveAssignmentSchema,
  ConnectorPlaylistBrowseSchema,
} from "#/api/generated/model";
import {
  applyAssignmentApiV1PlaylistAssignmentsAssignmentIdApplyPost,
  deleteAssignmentApiV1PlaylistAssignmentsAssignmentIdDelete,
  useCreateAndApplyAssignmentApiV1PlaylistAssignmentsPost,
} from "#/api/generated/playlist-assignments/playlist-assignments";
import { Button } from "#/components/ui/button";
import { Checkbox } from "#/components/ui/checkbox";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "#/components/ui/dropdown-menu";
import { toasts } from "#/lib/toasts";

import type { AssignMode } from "./AssignPlaylistDialog";
import { ImportStatusPill } from "./ImportStatusPill";
import { PlaylistArtwork } from "./PlaylistArtwork";
import { PlaylistMeta } from "./PlaylistMeta";
import { PreferenceBadge, type PreferenceState } from "./PreferenceToggle";
import { TagChip } from "./TagChip";

interface ConnectorPlaylistImportRowProps {
  playlist: ConnectorPlaylistBrowseSchema;
  /** Connector key — namespaces the checkbox id across mounted pickers. */
  connectorName: string;
  selected: boolean;
  onSelectedChange: (next: boolean) => void;
  /** Opens the tag/rate assignment dialog, which the picker owns. */
  onAssign: (mode: AssignMode, playlist: ConnectorPlaylistBrowseSchema) => void;
}

/**
 * Multi-select row of the connector playlist picker: checkbox, artwork, name
 * with its current tag/rating chips, import status, and an overflow menu that
 * owns the assignment actions.
 *
 * Re-apply and remove act on this playlist only, so their mutations live here
 * and their pending state is per-row.
 */
export function ConnectorPlaylistImportRow({
  playlist,
  connectorName,
  selected,
  onSelectedChange,
  onAssign,
}: ConnectorPlaylistImportRowProps) {
  const undoRemove = useCreateAndApplyAssignmentApiV1PlaylistAssignmentsPost({
    mutation: { meta: { errorLabel: "Undo failed" } },
  });

  const reApply = useMutation({
    mutationFn: () =>
      Promise.all(
        playlist.current_assignments.map((a) =>
          applyAssignmentApiV1PlaylistAssignmentsAssignmentIdApplyPost(
            a.assignment_id,
          ),
        ),
      ),
    onSuccess: (results) => {
      const tags = results.reduce(
        (sum, r) => sum + (r.status === 200 ? r.data.tags_applied : 0),
        0,
      );
      const prefs = results.reduce(
        (sum, r) => sum + (r.status === 200 ? r.data.preferences_applied : 0),
        0,
      );
      toasts.success(`Re-applied '${playlist.name}'`, {
        description:
          tags + prefs === 0
            ? "Nothing changed — playlist is in sync."
            : `${tags} tags · ${prefs} ratings refreshed.`,
      });
    },
    // The toast reports what changed, so the list must be current first.
    meta: {
      errorLabel: "Re-apply failed",
      invalidates: ["connector-playlists"],
      awaitInvalidation: true,
    },
  });

  const remove = useMutation({
    mutationFn: async (assignment: ActiveAssignmentSchema) => {
      await deleteAssignmentApiV1PlaylistAssignmentsAssignmentIdDelete(
        assignment.assignment_id,
      );
      return assignment;
    },
    onSuccess: (assignment) => {
      const isTag = assignment.action_type === "add_tag";
      toasts.success(
        isTag
          ? `${assignment.action_value} removed from '${playlist.name}'`
          : `Rating removed from '${playlist.name}'`,
        {
          description: "Tags you've added directly in Mixd are untouched.",
          action: {
            label: "Undo",
            onClick: () => {
              undoRemove.mutate({
                data: {
                  connector_playlist_id: playlist.connector_playlist_db_id,
                  action_type: assignment.action_type,
                  action_value: assignment.action_value,
                },
              });
            },
          },
        },
      );
    },
    // The undo toast names the assignment, so the list must be current first.
    meta: {
      errorLabel: "Failed to remove assignment",
      invalidates: ["connector-playlists"],
      awaitInvalidation: true,
    },
  });

  const rowId = `${connectorName}-pick-${playlist.connector_playlist_identifier}`;
  const tagAssignments = playlist.current_assignments.filter(
    (a) => a.action_type === "add_tag",
  );
  const ratingAssignment = playlist.current_assignments.find(
    (a) => a.action_type === "set_preference",
  );
  const hasAssignments = playlist.current_assignments.length > 0;

  return (
    <div className="flex items-center gap-3 border-b px-3 py-2 last:border-b-0 hover:bg-accent/30">
      <Checkbox
        id={rowId}
        checked={selected}
        onCheckedChange={(next) => onSelectedChange(next === true)}
      />
      <PlaylistArtwork src={playlist.image_url} />
      <label htmlFor={rowId} className="min-w-0 flex-1 cursor-pointer">
        <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="truncate font-medium text-text">
            {playlist.name}
          </span>
          {ratingAssignment && (
            <PreferenceBadge
              state={ratingAssignment.action_value as PreferenceState}
            />
          )}
          {tagAssignments.map((a) => (
            <TagChip
              key={a.assignment_id}
              tag={a.action_value}
              className="text-xs"
            />
          ))}
        </span>
        <PlaylistMeta
          owner={playlist.owner}
          trackCount={playlist.track_count}
        />
      </label>
      <ImportStatusPill status={playlist.import_status} />
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            variant="ghost"
            size="icon"
            aria-label={`More actions for ${playlist.name}`}
          >
            <MoreHorizontal />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem onSelect={() => onAssign("tag", playlist)}>
            Tag tracks…
          </DropdownMenuItem>
          <DropdownMenuItem onSelect={() => onAssign("rate", playlist)}>
            {ratingAssignment ? "Update rating…" : "Rate tracks…"}
          </DropdownMenuItem>
          {hasAssignments && (
            <>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                onSelect={() => reApply.mutate()}
                disabled={reApply.isPending}
              >
                Re-apply
              </DropdownMenuItem>
              {tagAssignments.map((a) => (
                <DropdownMenuItem
                  key={`remove-${a.assignment_id}`}
                  variant="destructive"
                  onSelect={() => remove.mutate(a)}
                >
                  Remove tag: {a.action_value}
                </DropdownMenuItem>
              ))}
              {ratingAssignment && (
                <DropdownMenuItem
                  variant="destructive"
                  onSelect={() => remove.mutate(ratingAssignment)}
                >
                  Remove rating
                </DropdownMenuItem>
              )}
            </>
          )}
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}
