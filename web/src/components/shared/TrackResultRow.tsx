import type { ReactNode } from "react";

import type { LibraryTrackSchema } from "#/api/generated/model";
import { ConnectorIcon } from "#/components/shared/ConnectorIcon";
import { formatArtists } from "#/lib/format";

interface TrackResultRowProps {
  track: LibraryTrackSchema;
  /** Rendered before the text block (e.g. a multi-select checkbox). */
  leading?: ReactNode;
  /** Rendered after the text block, before the connector icons (e.g. a badge). */
  trailing?: ReactNode;
}

/**
 * The title + artists/album + connector-icon row shared by every track
 * picker. Kept as a Fragment so it composes directly inside a flex
 * `Command.Item` — leading/trailing/connectors become sibling flex children,
 * matching the original inline markup exactly.
 */
export function TrackResultRow({
  track,
  leading,
  trailing,
}: TrackResultRowProps) {
  return (
    <>
      {leading}
      <div className="min-w-0 flex-1">
        <div className="truncate font-medium text-text">{track.title}</div>
        <div className="truncate text-xs text-text-muted">
          {formatArtists(track.artists)}
          {track.album && ` — ${track.album}`}
        </div>
      </div>
      {trailing}
      <div className="flex shrink-0 gap-1">
        {track.connector_names.map((name) => (
          <ConnectorIcon key={name} name={name} />
        ))}
      </div>
    </>
  );
}
