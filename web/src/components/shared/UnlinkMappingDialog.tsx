import { useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router";
import { toast } from "sonner";

import type { ConnectorMappingSchema } from "@/api/generated/model";
import {
  getGetTrackDetailApiV1TracksTrackIdGetQueryKey,
  useUnlinkMappingApiV1TracksTrackIdMappingsMappingIdDelete,
} from "@/api/generated/tracks/tracks";
import { MappingInfoCard } from "@/components/shared/MappingInfoCard";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

interface UnlinkMappingDialogProps {
  trackId: number;
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
          toast.success("Mapping unlinked", {
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
        onError: (error: Error) => {
          toast.error("Failed to unlink", { description: error.message });
        },
      },
    });

  const handleConfirm = () => {
    unlinkMutation.mutate({
      trackId,
      mappingId: mapping.mapping_id,
    });
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Unlink Mapping</DialogTitle>
          <DialogDescription>
            Remove this {mapping.connector_name} mapping from the track.
          </DialogDescription>
        </DialogHeader>

        <MappingInfoCard mapping={mapping} />

        <div className="rounded-md bg-status-error/10 p-3 text-sm text-text-muted">
          <strong className="text-text">This cannot be undone.</strong> The
          mapping will be permanently removed from this track. If no other
          mappings reference this external track, an orphan track will be
          auto-created.
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            size="sm"
            onClick={() => onOpenChange(false)}
          >
            Cancel
          </Button>
          <Button
            variant="destructive"
            size="sm"
            onClick={handleConfirm}
            disabled={unlinkMutation.isPending}
          >
            {unlinkMutation.isPending ? "Unlinking..." : "Unlink"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
