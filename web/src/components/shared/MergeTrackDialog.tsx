import { useQueryClient } from "@tanstack/react-query";
import { GitMerge } from "lucide-react";
import { useState } from "react";
import type {
  LibraryTrackSchema,
  TrackDetailSchema,
} from "#/api/generated/model";
import {
  getGetTrackDetailApiV1TracksTrackIdGetQueryKey,
  useMergeTrackApiV1TracksTrackIdMergePost,
} from "#/api/generated/tracks/tracks";
import { ConnectorIcon } from "#/components/shared/ConnectorIcon";
import { TrackSearchCombobox } from "#/components/shared/TrackSearchCombobox";
import { Badge } from "#/components/ui/badge";
import { Button } from "#/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "#/components/ui/dialog";
import { formatArtists } from "#/lib/format";
import { toasts } from "#/lib/toasts";

interface MergeTrackDialogProps {
  winner: TrackDetailSchema;
}

function TrackCard({
  title,
  artists,
  album,
  connectors,
  variant,
}: {
  title: string;
  artists: string;
  album?: string | null;
  connectors: string[];
  variant: "winner" | "loser";
}) {
  return (
    <div
      className={`rounded-lg border p-4 ${variant === "winner" ? "border-primary/40 bg-primary/5" : "border-border-muted bg-surface-sunken"}`}
    >
      <p className="mb-1 text-xs font-medium uppercase tracking-wider text-text-faint">
        {variant === "winner" ? "Keep (this track)" : "Merge into above"}
      </p>
      <p className="font-medium text-text">{title}</p>
      <p className="text-sm text-text-muted">{artists}</p>
      {album && <p className="text-xs text-text-faint">{album}</p>}
      <div className="mt-2 flex gap-1">
        {connectors.map((name) => (
          <ConnectorIcon key={name} name={name} />
        ))}
      </div>
    </div>
  );
}

export function MergeTrackDialog({ winner }: MergeTrackDialogProps) {
  const [open, setOpen] = useState(false);
  const [selectedLoser, setSelectedLoser] = useState<LibraryTrackSchema | null>(
    null,
  );

  const queryClient = useQueryClient();
  const mergeMutation = useMergeTrackApiV1TracksTrackIdMergePost({
    mutation: {
      onSuccess: () => {
        queryClient.invalidateQueries({
          queryKey: getGetTrackDetailApiV1TracksTrackIdGetQueryKey(winner.id),
        });
        toasts.success("Tracks merged", {
          description: `${selectedLoser?.title} has been merged into ${winner.title}.`,
        });
        setOpen(false);
        setSelectedLoser(null);
      },
      meta: { errorLabel: "Failed to merge tracks" },
    },
  });

  const handleConfirm = () => {
    if (!selectedLoser) return;
    mergeMutation.mutate({
      trackId: winner.id,
      data: { loser_id: selectedLoser.id },
    });
  };

  const handleOpenChange = (next: boolean) => {
    setOpen(next);
    if (!next) setSelectedLoser(null);
  };

  const winnerArtists = formatArtists(winner.artists);
  const winnerConnectors = winner.connector_mappings.map(
    (m) => m.connector_name,
  );
  const uniqueConnectors = [...new Set(winnerConnectors)];

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm">
          <GitMerge className="mr-1.5 size-3.5" />
          Merge with...
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Merge Duplicate Track</DialogTitle>
          <DialogDescription>
            Find the duplicate and merge it into this track. Play counts,
            service connections, and playlist entries will be combined.
          </DialogDescription>
        </DialogHeader>

        {!selectedLoser ? (
          <TrackSearchCombobox
            onSelect={setSelectedLoser}
            excludeTrackId={winner.id}
            placeholder="Search for the duplicate..."
          />
        ) : (
          <div className="space-y-4">
            <div className="grid grid-cols-1 gap-3">
              <TrackCard
                title={winner.title}
                artists={winnerArtists}
                album={winner.album}
                connectors={uniqueConnectors}
                variant="winner"
              />
              <TrackCard
                title={selectedLoser.title}
                artists={formatArtists(selectedLoser.artists)}
                album={selectedLoser.album}
                connectors={selectedLoser.connector_names}
                variant="loser"
              />
            </div>

            <div className="rounded-md bg-status-error/10 p-3 text-sm text-text-muted">
              <strong className="text-text">This cannot be undone.</strong>{" "}
              &ldquo;{selectedLoser.title}&rdquo; will be permanently merged
              into &ldquo;{winner.title}&rdquo;.
            </div>

            <div className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                className="flex-1"
                onClick={() => setSelectedLoser(null)}
              >
                Back to search
              </Button>
              <Button
                variant="destructive"
                size="sm"
                className="flex-1"
                onClick={handleConfirm}
                disabled={mergeMutation.isPending}
              >
                {mergeMutation.isPending ? "Merging..." : "Confirm Merge"}
              </Button>
            </div>
          </div>
        )}

        {!selectedLoser && (
          <DialogFooter>
            <Badge variant="outline" className="text-text-faint text-xs">
              Tip: Search by title to find the duplicate
            </Badge>
          </DialogFooter>
        )}
      </DialogContent>
    </Dialog>
  );
}
