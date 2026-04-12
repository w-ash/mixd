import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { getListTagsApiV1TagsGetQueryKey } from "#/api/generated/tags/tags";
import {
  getListTracksApiV1TracksGetQueryKey,
  useBatchTagTracksApiV1TracksTagsBatchPost,
} from "#/api/generated/tracks/tracks";
import { toasts } from "#/lib/toasts";

import { ConfirmationDialog } from "./ConfirmationDialog";
import { TagAutocomplete } from "./TagAutocomplete";
import { TagChip } from "./TagChip";

interface BulkTagDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  trackIds: string[];
  /** Called after a successful batch-tag so the caller can clear selection. */
  onTagged?: () => void;
}

export function BulkTagDialog({
  open,
  onOpenChange,
  trackIds,
  onTagged,
}: BulkTagDialogProps) {
  const [draftTag, setDraftTag] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const batchTag = useBatchTagTracksApiV1TracksTagsBatchPost({
    mutation: {
      onSuccess: (response) => {
        if (response.status !== 200) return;
        const { tag, requested, tagged } = response.data;
        queryClient.invalidateQueries({
          queryKey: getListTracksApiV1TracksGetQueryKey(),
        });
        queryClient.invalidateQueries({
          queryKey: getListTagsApiV1TagsGetQueryKey(),
        });
        toasts.success(
          tagged === requested
            ? `Tagged ${tagged} track${tagged === 1 ? "" : "s"} with ${tag}`
            : `Tagged ${tagged} of ${requested} (others already had ${tag})`,
        );
        onTagged?.();
        onOpenChange(false);
        setDraftTag(null);
      },
      meta: { errorLabel: "Failed to tag tracks" },
    },
  });

  const handleOpenChange = (next: boolean) => {
    if (!next) setDraftTag(null);
    onOpenChange(next);
  };

  const countLabel = `${trackIds.length} track${trackIds.length === 1 ? "" : "s"}`;

  return (
    <ConfirmationDialog
      open={open}
      onOpenChange={handleOpenChange}
      title={`Tag ${countLabel}`}
      description="Add one tag to every selected track. Tracks that already have the tag are left alone."
      confirmLabel={`Tag ${countLabel}`}
      disabled={!draftTag}
      isPending={batchTag.isPending}
      onConfirm={() => {
        if (!draftTag) return;
        batchTag.mutate({ data: { track_ids: trackIds, tag: draftTag } });
      }}
    >
      {draftTag ? (
        <TagChip tag={draftTag} onRemove={() => setDraftTag(null)} />
      ) : (
        <TagAutocomplete
          onAdd={(raw) => setDraftTag(raw.trim().toLowerCase())}
          autoFocus
          placeholder="Pick or add a tag…"
        />
      )}
    </ConfirmationDialog>
  );
}
