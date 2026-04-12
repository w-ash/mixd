import { useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router";
import type { ConnectorMappingSchema } from "#/api/generated/model";
import {
  getGetTrackDetailApiV1TracksTrackIdGetQueryKey,
  useUnlinkMappingApiV1TracksTrackIdMappingsMappingIdDelete,
} from "#/api/generated/tracks/tracks";
import { ConfirmationDialog } from "#/components/shared/ConfirmationDialog";
import { MappingInfoCard } from "#/components/shared/MappingInfoCard";
import { toasts } from "#/lib/toasts";

interface UnlinkMappingDialogProps {
  trackId: string;
  mapping: ConnectorMappingSchema;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function UnlinkMappingDialog({
  trackId,
  mapping,
  open,
  onOpenChange,
}: UnlinkMappingDialogProps) {
  const queryClient = useQueryClient();
  const unlinkMutation =
    useUnlinkMappingApiV1TracksTrackIdMappingsMappingIdDelete({
      mutation: {
        onSuccess: (response) => {
          queryClient.invalidateQueries({
            queryKey: getGetTrackDetailApiV1TracksTrackIdGetQueryKey(trackId),
          });
          const orphanId =
            response.status === 200 ? response.data.orphan_track_id : null;
          toasts.success("Mapping unlinked", {
            description: orphanId ? (
              <span>
                An orphan track was created.{" "}
                <Link to={`/library/${orphanId}`} className="underline">
                  View it
                </Link>
              </span>
            ) : (
              "The mapping has been removed."
            ),
          });
          onOpenChange(false);
        },
        meta: { errorLabel: "Failed to unlink" },
      },
    });

  return (
    <ConfirmationDialog
      open={open}
      onOpenChange={onOpenChange}
      title="Unlink Mapping"
      description={`Remove this ${mapping.connector_name} mapping from the track.`}
      confirmLabel={unlinkMutation.isPending ? "Unlinking..." : "Unlink"}
      destructive
      isPending={unlinkMutation.isPending}
      onConfirm={() =>
        unlinkMutation.mutate({ trackId, mappingId: mapping.mapping_id })
      }
    >
      <MappingInfoCard mapping={mapping} />

      <div className="rounded-md bg-status-error/10 p-3 text-sm text-text-muted">
        <strong className="text-text">This cannot be undone.</strong> The
        mapping will be permanently removed from this track. If no other
        mappings reference this external track, an orphan track will be
        auto-created.
      </div>
    </ConfirmationDialog>
  );
}
