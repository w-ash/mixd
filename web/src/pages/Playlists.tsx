import { Download, Music } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router";

import { useGetConnectorsApiV1ConnectorsGet } from "#/api/generated/connectors/connectors";
import type { PlaylistSummarySchema } from "#/api/generated/model";
import { useListPlaylistsApiV1PlaylistsGet } from "#/api/generated/playlists/playlists";
import { STALE } from "#/api/query-client";
import { PageHeader } from "#/components/layout/PageHeader";
import { ConnectorIcon } from "#/components/shared/ConnectorIcon";
import {
  ConnectorPlaylistPickerDialog,
  type PickedPlaylist,
} from "#/components/shared/ConnectorPlaylistPickerDialog";
import { CreatePlaylistModal } from "#/components/shared/CreatePlaylistModal";
import { EmptyState } from "#/components/shared/EmptyState";
import { ImportPlaylistsConfirmDialog } from "#/components/shared/ImportPlaylistsConfirmDialog";
import { QueryErrorState } from "#/components/shared/QueryErrorState";
import { ResponsiveTable } from "#/components/shared/ResponsiveTable";
import { TablePagination } from "#/components/shared/TablePagination";
import { TitleLink } from "#/components/shared/TitleLink";
import { Button } from "#/components/ui/button";
import { Skeleton } from "#/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "#/components/ui/table";
import { usePagination } from "#/hooks/usePagination";
import { decodeHtmlEntities, formatDate } from "#/lib/format";
import { pluralSuffix } from "#/lib/pluralize";
import { getSyncStatusConfig } from "#/lib/sync-status";

function PlaylistTableSkeleton() {
  return (
    <div className="space-y-3">
      {Array.from({ length: 5 }).map((_, i) => (
        // biome-ignore lint/suspicious/noArrayIndexKey: static skeleton placeholders
        <div key={i} className="flex items-center gap-4">
          <Skeleton className="h-5 w-48" />
          <Skeleton className="h-5 w-16" />
          <Skeleton className="h-5 w-24" />
          <Skeleton className="h-5 w-32" />
        </div>
      ))}
    </div>
  );
}

/**
 * Card representation of a Playlist row — used by ResponsiveTable below the
 * @2xl container threshold (typically iPhone / iPad portrait widths).
 */
function PlaylistCard({ playlist }: { playlist: PlaylistSummarySchema }) {
  return (
    <article className="flex items-start gap-3 rounded-md border border-border bg-surface px-3 py-3">
      <div className="min-w-0 flex-1">
        <TitleLink to={`/playlists/${playlist.id}`} viewTransition>
          {playlist.name}
        </TitleLink>
        {playlist.description && (
          <p className="mt-0.5 line-clamp-1 text-xs text-text-muted">
            {decodeHtmlEntities(playlist.description)}
          </p>
        )}
        <div className="mt-1.5 flex items-center gap-3 text-xs text-text-muted">
          <span className="tabular-nums">
            {playlist.track_count} track{pluralSuffix(playlist.track_count)}
          </span>
          <span aria-hidden="true">·</span>
          <span>Updated {formatDate(playlist.updated_at)}</span>
        </div>
        {playlist.connector_links.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-2">
            {playlist.connector_links.map((link) => (
              <span
                key={link.connector_name}
                className="inline-flex items-center gap-1.5"
              >
                <ConnectorIcon name={link.connector_name} labelHidden />
                <span
                  className={`size-1.5 rounded-full ${getSyncStatusConfig(link.sync_status).dotClass}`}
                  title={`${link.sync_direction} · ${link.sync_status}`}
                />
              </span>
            ))}
          </div>
        )}
      </div>
    </article>
  );
}

export function Playlists() {
  const { page, limit, offset, setPage } = usePagination(0);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [pendingImport, setPendingImport] = useState<PickedPlaylist[]>([]);

  const { data: connectorsData } = useGetConnectorsApiV1ConnectorsGet({
    query: { staleTime: STALE.STATIC },
  });

  const importSource = useMemo(() => {
    const connectors =
      connectorsData?.status === 200 ? connectorsData.data : [];
    return connectors.find((c) => c.capabilities.includes("playlist_import"));
  }, [connectorsData]);

  const { data, isLoading, isError, error } = useListPlaylistsApiV1PlaylistsGet(
    { limit, offset },
    { query: { staleTime: STALE.MEDIUM, placeholderData: (prev) => prev } },
  );

  const response = data?.status === 200 ? data.data : undefined;
  const playlists = response?.data ?? [];
  const total = response?.total ?? 0;
  const totalPages = total > 0 ? Math.ceil(total / limit) : 1;

  return (
    <div>
      <title>Playlists — Mixd</title>
      <PageHeader
        title="Playlists"
        description="Your canonical playlists across all services."
        action={
          <div className="flex items-center gap-2">
            {importSource && (
              <Button variant="outline" onClick={() => setPickerOpen(true)}>
                <Download />
                Import from {importSource.display_name}
              </Button>
            )}
            <CreatePlaylistModal />
          </div>
        }
      />
      {importSource && (
        <ConnectorPlaylistPickerDialog
          open={pickerOpen}
          onOpenChange={setPickerOpen}
          connector={importSource}
          onConfirm={(picked) => {
            setPendingImport(picked);
            setPickerOpen(false);
          }}
        />
      )}
      {importSource && (
        <ImportPlaylistsConfirmDialog
          open={pendingImport.length > 0}
          onOpenChange={(open) => {
            if (!open) setPendingImport([]);
          }}
          connector={importSource}
          playlists={pendingImport}
          onImported={() => setPendingImport([])}
        />
      )}

      {isLoading && <PlaylistTableSkeleton />}

      {isError && (
        <QueryErrorState error={error} heading="Failed to load playlists" />
      )}

      {!isLoading && !isError && playlists.length === 0 && (
        <EmptyState
          icon={<Music className="size-10" />}
          heading="No playlists yet"
          description="Create your first playlist to start curating your music collection."
          action={<CreatePlaylistModal />}
        />
      )}

      {!isLoading && !isError && playlists.length > 0 && (
        <>
          <ResponsiveTable
            cards={
              <div className="flex flex-col gap-2">
                {playlists.map((playlist) => (
                  <PlaylistCard key={playlist.id} playlist={playlist} />
                ))}
              </div>
            }
            table={
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Name</TableHead>
                    <TableHead className="w-24 text-right">Tracks</TableHead>
                    <TableHead className="w-40">Connectors</TableHead>
                    <TableHead className="w-36 text-right">Updated</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {playlists.map((playlist) => (
                    <TableRow key={playlist.id}>
                      <TableCell>
                        <Link
                          to={`/playlists/${playlist.id}`}
                          viewTransition
                          className="font-medium text-text hover:text-primary transition-colors"
                        >
                          {playlist.name}
                        </Link>
                        {playlist.description && (
                          <p className="mt-0.5 text-xs text-text-muted line-clamp-1">
                            {decodeHtmlEntities(playlist.description)}
                          </p>
                        )}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {playlist.track_count}
                      </TableCell>
                      <TableCell>
                        <div className="flex gap-2">
                          {playlist.connector_links.map((link) => (
                            <span
                              key={link.connector_name}
                              className="inline-flex items-center gap-1.5"
                            >
                              <ConnectorIcon
                                name={link.connector_name}
                                labelHidden
                              />
                              <span
                                className={`size-1.5 rounded-full ${getSyncStatusConfig(link.sync_status).dotClass}`}
                                title={`${link.sync_direction} · ${link.sync_status}`}
                              />
                            </span>
                          ))}
                        </div>
                      </TableCell>
                      <TableCell className="text-right text-text-muted text-sm">
                        {formatDate(playlist.updated_at)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            }
          />

          <TablePagination
            page={Math.min(page, totalPages)}
            totalPages={totalPages}
            total={total}
            limit={limit}
            onPageChange={setPage}
          />
        </>
      )}
    </div>
  );
}
