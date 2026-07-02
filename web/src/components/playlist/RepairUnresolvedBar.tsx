import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Loader2 } from "lucide-react";
import {
  getGetPlaylistTracksApiV1PlaylistsPlaylistIdTracksGetQueryKey,
  useRepairPlaylistUnresolvedApiV1PlaylistsPlaylistIdRepairPost,
} from "#/api/generated/playlists/playlists";
import { Button } from "#/components/ui/button";
import { pluralize } from "#/lib/pluralize";
import { toasts } from "#/lib/toasts";

/** Roll-up + bulk repair for a playlist's unresolved entries. Silent at zero. */
export function RepairUnresolvedBar({
  playlistId,
  count,
}: {
  playlistId: string;
  count: number;
}) {
  const queryClient = useQueryClient();
  const repairMut =
    useRepairPlaylistUnresolvedApiV1PlaylistsPlaylistIdRepairPost({
      mutation: {
        onSuccess: (res) => {
          if (res.status !== 200) return;
          const { repaired, still_unresolved } = res.data;
          if (repaired === 0) {
            toasts.info("No new matches found yet");
          } else if (still_unresolved === 0) {
            toasts.success(`Repaired ${pluralize(repaired, "track")}`);
          } else {
            toasts.success(
              `Repaired ${repaired} · ${still_unresolved} still unresolved`,
            );
          }
          queryClient.invalidateQueries({
            queryKey:
              getGetPlaylistTracksApiV1PlaylistsPlaylistIdTracksGetQueryKey(
                playlistId,
              ),
          });
        },
        meta: { errorLabel: "Repair failed" },
      },
    });

  if (count === 0) return null;

  return (
    <div className="mb-6 flex flex-wrap items-center justify-between gap-3 rounded-md border-l-2 border-status-expired bg-surface-inset px-4 py-3">
      <div className="flex items-center gap-2 text-sm text-text-muted">
        <AlertTriangle
          className="size-4 shrink-0 text-status-expired"
          aria-hidden="true"
        />
        <span>{pluralize(count, "track")} couldn't be matched.</span>
      </div>
      <Button
        variant="outline"
        size="sm"
        disabled={repairMut.isPending}
        onClick={() => repairMut.mutate({ playlistId })}
      >
        {repairMut.isPending && (
          <Loader2 className="mr-1 size-3 animate-spin" />
        )}
        Repair unresolved ({count})
      </Button>
    </div>
  );
}
