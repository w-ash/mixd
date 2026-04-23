import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { getListTagsApiV1TagsGetQueryKey } from "#/api/generated/tags/tags";
import {
  getListTracksApiV1TracksGetQueryKey,
  useBatchTagTracksApiV1TracksTagsBatchPost,
} from "#/api/generated/tracks/tracks";
import { pluralize } from "#/lib/pluralize";
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
        queryClient.invalidateQueries({
          queryKey: getListTracksApiV1TracksGetQueryKey(),
        });
        queryClient.invalidateQueries({
          queryKey: getListTagsApiV1TagsGetQueryKey(),
        });
        onTagged?.();
        onOpenChange(false);
        setDraftTag(null);
      },
      // toasts.promise (called inline below) emits its own error toast; the
      // global mutation error handler would double-toast otherwise.
      meta: { suppressErrorToast: true },
    },
  });

  const handleOpenChange = (next: boolean) => {
    if (!next) setDraftTag(null);
    onOpenChange(next);
  };

  const countLabel = pluralize(trackIds.length, "track");

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
        toasts.promise(
          batchTag.mutateAsync({
            data: { track_ids: trackIds, tag: draftTag },
          }),
          {
            loading: `Tagging ${countLabel}…`,
            success: (resp) => {
              // customFetch throws on non-2xx; the status check exists only
              // to narrow the discriminated-union response type.
              if (resp.status !== 200) return "Tagged";
              const { tag, requested, tagged } = resp.data;
              return tagged === requested
                ? `Tagged ${pluralize(tagged, "track")} with ${tag}`
                : `Tagged ${tagged} of ${requested} (others already had ${tag})`;
            },
            error: "Failed to tag tracks",
          },
        );
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
