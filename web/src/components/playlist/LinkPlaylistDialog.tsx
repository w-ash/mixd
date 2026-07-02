import { useQueryClient } from "@tanstack/react-query";
import { Link2, ListMusic, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { useGetConnectorsApiV1ConnectorsGet } from "#/api/generated/connectors/connectors";
import { useCreatePlaylistLinkApiV1PlaylistsPlaylistIdLinksPost } from "#/api/generated/playlists/playlists";
import { STALE } from "#/api/query-client";
import { ConnectorPlaylistPickerDialog } from "#/components/shared/ConnectorPlaylistPickerDialog";
import { DirectionChooser } from "#/components/shared/DirectionChooser";
import { Button } from "#/components/ui/button";
import {
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "#/components/ui/dialog";
import { Input } from "#/components/ui/input";
import { ResponsiveDialog } from "#/components/ui/responsive-dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "#/components/ui/select";
import { getConnectorLabel } from "#/lib/connector-brand";
import type { SyncDirection } from "#/lib/sync-direction";
import { toasts } from "#/lib/toasts";
import { invalidateLinkQueries } from "./link-queries";

export function LinkPlaylistDialog({ playlistId }: { playlistId: string }) {
  const [open, setOpen] = useState(false);
  const [playlistInput, setPlaylistInput] = useState("");
  // Friendly name of a playlist chosen via Browse — labels the browse button so
  // the user sees "Selected: …" instead of only the raw identifier in the input.
  const [pickedName, setPickedName] = useState<string | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [direction, setDirection] = useState<SyncDirection>("push");
  const queryClient = useQueryClient();

  const { data: connectorsData } = useGetConnectorsApiV1ConnectorsGet({
    query: { staleTime: STALE.STATIC },
  });

  // Only connectors that advertise ``playlist_sync`` can accept a link —
  // MusicBrainz and Apple Music (coming soon) are filtered out automatically.
  const linkableConnectors =
    connectorsData?.status === 200
      ? connectorsData.data.filter((c) =>
          c.capabilities.includes("playlist_sync"),
        )
      : [];

  const defaultConnector = linkableConnectors[0]?.name ?? "";
  const [connector, setConnector] = useState(defaultConnector);

  // Ensure the state reflects the first real connector once the query lands.
  useEffect(() => {
    if (!connector && defaultConnector) setConnector(defaultConnector);
  }, [connector, defaultConnector]);

  const selectedConnector = linkableConnectors.find(
    (c) => c.name === connector,
  );
  // Browse-to-pick reuses the import browse list, which needs the
  // ``playlist_import`` capability (GET /connectors/{service}/playlists). A
  // sync-only connector without it falls back to paste-an-ID.
  const canBrowse =
    selectedConnector?.capabilities.includes("playlist_import") ?? false;
  const placeholder = selectedConnector
    ? `Paste ${selectedConnector.display_name} URL or playlist ID`
    : "Paste playlist URL or ID";

  const createLink = useCreatePlaylistLinkApiV1PlaylistsPlaylistIdLinksPost({
    mutation: {
      onSuccess: () => {
        invalidateLinkQueries(queryClient, playlistId);
        setOpen(false);
        setPlaylistInput("");
        toasts.success("Playlist linked");
      },
      meta: { errorLabel: "Failed to link playlist" },
    },
  });

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!playlistInput.trim()) return;
    createLink.mutate({
      playlistId,
      data: {
        connector,
        connector_playlist_identifier: playlistInput.trim(),
        sync_direction: direction,
      },
    });
  }

  return (
    <>
      <ResponsiveDialog
        open={open}
        onOpenChange={(isOpen) => {
          setOpen(isOpen);
          if (isOpen) {
            setPlaylistInput("");
            setPickedName(null);
            setPickerOpen(false);
            setConnector(defaultConnector);
            setDirection("push");
          }
        }}
        trigger={
          <Button variant="outline" size="sm">
            <Link2 className="mr-1.5 size-3.5" />
            Link Playlist
          </Button>
        }
      >
        <form onSubmit={handleSubmit}>
          <DialogHeader>
            <DialogTitle>Link External Playlist</DialogTitle>
            <DialogDescription>
              Connect this playlist to an external service for syncing.
            </DialogDescription>
          </DialogHeader>

          <div className="mt-4 space-y-4">
            <div className="space-y-2">
              <label
                htmlFor="link-connector"
                className="text-sm font-medium text-text"
              >
                Service
              </label>
              <Select
                value={connector}
                onValueChange={(next) => {
                  setConnector(next);
                  // A picked/typed playlist belongs to the previous service —
                  // drop it so a switch can't submit one service's identifier
                  // against another.
                  setPlaylistInput("");
                  setPickedName(null);
                }}
              >
                <SelectTrigger id="link-connector">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {linkableConnectors.map((c) => (
                    <SelectItem key={c.name} value={c.name}>
                      {c.display_name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <span className="text-sm font-medium text-text">Playlist</span>
              {canBrowse && (
                <Button
                  type="button"
                  variant="outline"
                  className="w-full justify-start"
                  onClick={() => setPickerOpen(true)}
                >
                  <ListMusic className="mr-1.5 size-3.5" />
                  {pickedName
                    ? `Selected: ${pickedName}`
                    : `Browse ${selectedConnector?.display_name ?? ""} playlists`}
                </Button>
              )}
              <Input
                id="link-playlist-id"
                value={playlistInput}
                onChange={(e) => {
                  setPlaylistInput(e.target.value);
                  // Typing overrides a browsed pick — drop the stale name label.
                  if (pickedName) setPickedName(null);
                }}
                placeholder={placeholder}
                required
                autoFocus={!canBrowse}
              />
              <p className="text-xs text-text-muted font-body">
                {canBrowse
                  ? "Or paste a playlist URL, URI, or raw ID."
                  : "Accepts a playlist URL, URI, or raw ID."}{" "}
                The playlist will be validated immediately.
              </p>
            </div>
            <DirectionChooser
              value={direction}
              onChange={setDirection}
              connectorLabel={getConnectorLabel(connector)}
              legend="Sync Direction"
            />
          </div>

          <DialogFooter className="mt-6">
            <Button
              type="button"
              variant="outline"
              onClick={() => setOpen(false)}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={!playlistInput.trim() || createLink.isPending}
            >
              {createLink.isPending ? (
                <>
                  <Loader2 className="mr-1.5 size-3.5 animate-spin" />
                  Validating...
                </>
              ) : (
                "Link"
              )}
            </Button>
          </DialogFooter>
        </form>
      </ResponsiveDialog>
      {selectedConnector && (
        <ConnectorPlaylistPickerDialog
          open={pickerOpen}
          onOpenChange={setPickerOpen}
          connector={selectedConnector}
          mode="select"
          onConfirm={(picked) => {
            const first = picked[0];
            if (first) {
              setPlaylistInput(first.id);
              setPickedName(first.name);
            }
            setPickerOpen(false);
          }}
        />
      )}
    </>
  );
}
