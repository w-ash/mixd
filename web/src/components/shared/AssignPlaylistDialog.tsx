import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { getListSpotifyPlaylistsApiV1ConnectorsSpotifyPlaylistsGetQueryKey } from "#/api/generated/connectors/connectors";
import type {
  ApplyResultSchema,
  SpotifyPlaylistBrowseSchema,
} from "#/api/generated/model";
import { useCreateAndApplyAssignmentApiV1PlaylistAssignmentsPost } from "#/api/generated/playlist-assignments/playlist-assignments";
import { Button } from "#/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "#/components/ui/dialog";
import { toasts } from "#/lib/toasts";

import { type PreferenceState, PreferenceToggle } from "./PreferenceToggle";
import { TagAutocomplete } from "./TagAutocomplete";

export type AssignMode = "tag" | "rate";

interface AssignPlaylistDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  mode: AssignMode;
  playlist: Pick<
    SpotifyPlaylistBrowseSchema,
    "connector_playlist_db_id" | "name" | "current_assignments"
  >;
}

function summarizeApply(result: ApplyResultSchema, mode: AssignMode): string {
  if (mode === "tag") {
    const n = result.tags_applied;
    return n === 0
      ? "No new tracks to tag."
      : `Tagged ${n} ${n === 1 ? "track" : "tracks"}.`;
  }
  const n = result.preferences_applied;
  return n === 0
    ? "No new tracks to rate."
    : `Rated ${n} ${n === 1 ? "track" : "tracks"}.`;
}

export function AssignPlaylistDialog({
  open,
  onOpenChange,
  mode,
  playlist,
}: AssignPlaylistDialogProps) {
  const queryClient = useQueryClient();
  const existingTagValues = playlist.current_assignments
    .filter((a) => a.action_type === "add_tag")
    .map((a) => a.action_value);
  const existingRating = playlist.current_assignments.find(
    (a) => a.action_type === "set_preference",
  )?.action_value as PreferenceState | undefined;

  // Initialised once per mount — the picker conditionally renders this
  // dialog (`{assignDialog && ...}`) so state resets naturally between opens.
  const [rating, setRating] = useState<PreferenceState | null>(
    existingRating ?? null,
  );

  const create = useCreateAndApplyAssignmentApiV1PlaylistAssignmentsPost({
    mutation: {
      onSuccess: async (response) => {
        await queryClient.invalidateQueries({
          queryKey:
            getListSpotifyPlaylistsApiV1ConnectorsSpotifyPlaylistsGetQueryKey(),
        });
        if (response.status === 201) {
          const value = response.data.assignment.action_value;
          toasts.success(`${value} → '${playlist.name}'`, {
            description: summarizeApply(response.data.result, mode),
          });
          onOpenChange(false);
        }
      },
      meta: { errorLabel: "Failed to save assignment" },
    },
  });

  const isTag = mode === "tag";
  const title = isTag
    ? `Tag tracks in '${playlist.name}'`
    : `Rate tracks in '${playlist.name}'`;
  const description = isTag
    ? "Every track in this playlist will gain the tag you pick. Removing a track from the playlist on Spotify will remove the tag here on the next re-apply."
    : "Every track in this playlist will get the rating you pick. Manual ratings you've set yourself are never overwritten.";

  const handleAddTag = (rawTag: string) => {
    create.mutate({
      data: {
        connector_playlist_id: playlist.connector_playlist_db_id,
        action_type: "add_tag",
        action_value: rawTag,
      },
    });
  };

  const handleConfirmRating = () => {
    if (rating === null || rating === existingRating) return;
    create.mutate({
      data: {
        connector_playlist_id: playlist.connector_playlist_db_id,
        action_type: "set_preference",
        action_value: rating,
      },
    });
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>

        {isTag ? (
          <TagAutocomplete
            autoFocus
            exclude={existingTagValues}
            onAdd={handleAddTag}
            placeholder="Type a tag (e.g. mood:chill)"
          />
        ) : (
          <div className="flex items-center justify-center py-2">
            <PreferenceToggle value={rating} onChange={setRating} />
          </div>
        )}

        <DialogFooter>
          <Button
            variant="ghost"
            onClick={() => onOpenChange(false)}
            disabled={create.isPending}
          >
            Cancel
          </Button>
          {!isTag && (
            <Button
              onClick={handleConfirmRating}
              disabled={
                create.isPending || rating === null || rating === existingRating
              }
            >
              {existingRating ? "Update rating" : "Rate tracks"}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
