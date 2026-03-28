import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import type {
  ConnectorMappingSchema,
  LibraryTrackSchema,
} from "@/api/generated/model";
import {
  getGetTrackDetailApiV1TracksTrackIdGetQueryKey,
  useRelinkMappingApiV1TracksTrackIdMappingsMappingIdPatch,
} from "@/api/generated/tracks/tracks";
import { ConnectorIcon } from "@/components/shared/ConnectorIcon";
import { MappingInfoCard } from "@/components/shared/MappingInfoCard";
import { TrackSearchCombobox } from "@/components/shared/TrackSearchCombobox";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { formatArtists } from "@/lib/format";

interface RelinkMappingDialogProps {
  trackId: string;
  mapping: ConnectorMappingSchema;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function RelinkMappingDialog({
  trackId,
  mapping,
  open,
  onOpenChange,
}: RelinkMappingDialogProps) {
  const [selectedTarget, setSelectedTarget] =
    useState<LibraryTrackSchema | null>(null);

  const queryClient = useQueryClient();
  const relinkMutation =
    useRelinkMappingApiV1TracksTrackIdMappingsMappingIdPatch({
      mutation: {
        onSuccess: () => {
          queryClient.invalidateQueries({
            queryKey: getGetTrackDetailApiV1TracksTrackIdGetQueryKey(trackId),
          });
          if (selectedTarget) {
            queryClient.invalidateQueries({
              queryKey: getGetTrackDetailApiV1TracksTrackIdGetQueryKey(
                selectedTarget.id,
              ),
            });
          }
          toast.success("Mapping relinked", {
            description: `Moved to "${selectedTarget?.title}".`,
          });
          onOpenChange(false);
        },
        onError: (error: Error) => {
          toast.error("Failed to relink", { description: error.message });
        },
      },
    });

  const handleConfirm = () => {
    if (!selectedTarget) return;
    relinkMutation.mutate({
      trackId,
      mappingId: mapping.mapping_id,
      data: { new_track_id: selectedTarget.id },
    });
  };

  const handleOpenChange = (next: boolean) => {
    onOpenChange(next);
    if (!next) setSelectedTarget(null);
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Relink Mapping</DialogTitle>
          <DialogDescription>
            Move this {mapping.connector_name} mapping to a different canonical
            track. The mapping will be marked as a manual override.
          </DialogDescription>
        </DialogHeader>

        <MappingInfoCard mapping={mapping} />

        {!selectedTarget ? (
          <TrackSearchCombobox
            onSelect={setSelectedTarget}
            excludeTrackId={trackId}
            placeholder="Search for the target track..."
          />
        ) : (
          <div className="space-y-4">
            <div className="rounded-lg border border-primary/40 bg-primary/5 p-4">
              <p className="mb-1 text-xs font-medium uppercase tracking-wider text-text-faint">
                Move to
              </p>
              <p className="font-medium text-text">{selectedTarget.title}</p>
              <p className="text-sm text-text-muted">
                {formatArtists(selectedTarget.artists)}
              </p>
              {selectedTarget.album && (
                <p className="text-xs text-text-faint">
                  {selectedTarget.album}
                </p>
              )}
              <div className="mt-2 flex gap-1">
                {selectedTarget.connector_names.map((name) => (
                  <ConnectorIcon key={name} name={name} />
                ))}
              </div>
            </div>

            <div className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                className="flex-1"
                onClick={() => setSelectedTarget(null)}
              >
                Back to search
              </Button>
              <Button
                size="sm"
                className="flex-1"
                onClick={handleConfirm}
                disabled={relinkMutation.isPending}
              >
                {relinkMutation.isPending ? "Relinking..." : "Relink"}
              </Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
