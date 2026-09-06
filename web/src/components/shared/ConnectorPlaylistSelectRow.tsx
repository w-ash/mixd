import type { ConnectorPlaylistBrowseSchema } from "#/api/generated/model";

import { ImportStatusPill } from "./ImportStatusPill";
import { PlaylistArtwork } from "./PlaylistArtwork";
import { PlaylistMeta } from "./PlaylistMeta";

interface ConnectorPlaylistSelectRowProps {
  playlist: ConnectorPlaylistBrowseSchema;
  onSelect: (playlist: ConnectorPlaylistBrowseSchema) => void;
}

/**
 * Single-select row of the connector playlist picker: the whole row is the
 * affordance — one click confirms this playlist. Checkbox and assignment menu
 * are import-only chrome and stay out.
 */
export function ConnectorPlaylistSelectRow({
  playlist,
  onSelect,
}: ConnectorPlaylistSelectRowProps) {
  return (
    <button
      type="button"
      onClick={() => onSelect(playlist)}
      className="flex w-full items-center gap-3 border-b px-3 py-2 text-left last:border-b-0 hover:bg-accent/30"
    >
      <PlaylistArtwork src={playlist.image_url} />
      <span className="min-w-0 flex-1">
        <span className="block truncate font-medium text-text">
          {playlist.name}
        </span>
        <PlaylistMeta
          owner={playlist.owner}
          trackCount={playlist.track_count}
        />
      </span>
      <ImportStatusPill status={playlist.import_status} />
    </button>
  );
}
