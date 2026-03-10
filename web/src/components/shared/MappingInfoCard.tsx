import type { ConnectorMappingSchema } from "@/api/generated/model";
import { ConnectorIcon } from "@/components/shared/ConnectorIcon";

/** Compact display of a connector mapping's title, artists, and icon. */
export function MappingInfoCard({
  mapping,
}: {
  mapping: ConnectorMappingSchema;
}) {
  return (
    <div className="rounded-md border border-border-muted bg-surface-sunken px-3 py-2">
      <div className="flex items-center gap-2">
        <ConnectorIcon name={mapping.connector_name} />
        <div className="min-w-0 flex-1">
          <span className="text-sm font-medium text-text">
            {mapping.connector_track_title || mapping.connector_track_id}
          </span>
          {mapping.connector_track_artists.length > 0 && (
            <span className="ml-1.5 text-xs text-text-muted">
              {mapping.connector_track_artists.join(", ")}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
