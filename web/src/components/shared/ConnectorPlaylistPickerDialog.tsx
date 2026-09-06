import { useMutation } from "@tanstack/react-query";
import { Loader2, RefreshCw, Search } from "lucide-react";
import { useMemo, useState } from "react";

import {
  listConnectorPlaylistsApiV1ConnectorsServicePlaylistsGet,
  useListConnectorPlaylistsApiV1ConnectorsServicePlaylistsGet,
} from "#/api/generated/connectors/connectors";
import type {
  ConnectorMetadataSchema,
  ConnectorPlaylistBrowseSchema,
} from "#/api/generated/model";
import { Button } from "#/components/ui/button";
import { Checkbox } from "#/components/ui/checkbox";
import {
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "#/components/ui/dialog";
import { Input } from "#/components/ui/input";
import { ResponsiveDialog } from "#/components/ui/responsive-dialog";
import { Skeleton } from "#/components/ui/skeleton";
import { useSelectionSet } from "#/hooks/useSelectionSet";
import { useTrackSearch } from "#/hooks/useTrackSearch";
import { pluralize } from "#/lib/pluralize";
import { cn } from "#/lib/utils";

import { type AssignMode, AssignPlaylistDialog } from "./AssignPlaylistDialog";
import { ConnectorPlaylistImportRow } from "./ConnectorPlaylistImportRow";
import { ConnectorPlaylistSelectRow } from "./ConnectorPlaylistSelectRow";
import { EmptyState } from "./EmptyState";
import { QueryStates } from "./QueryStates";

/**
 * On-demand picker: opened contextually from action buttons (Playlists
 * page today, tag-mapping flows in v0.7.4). Not a persistent route.
 *
 * Cache-first list + client-side filter (Spotify has no name search on
 * /me/playlists). "Refresh" forces a fetch + cache upsert. Selection spans
 * the whole list, so rows picked under one search survive the next; the
 * header checkbox still speaks only for the rows on screen. It resets on
 * close and emits via onConfirm.
 */

type StatusFilter = "all" | "not_imported" | "imported";
type AttributeFilter = "all" | "collaborative" | "public";

const STATUS_FILTERS: ReadonlyArray<readonly [StatusFilter, string]> = [
  ["all", "All"],
  ["not_imported", "Not imported"],
  ["imported", "Imported"],
];

const ATTRIBUTE_FILTERS: ReadonlyArray<readonly [AttributeFilter, string]> = [
  ["all", "Any kind"],
  ["collaborative", "Collaborative"],
  ["public", "Public"],
];

const NO_PLAYLISTS: ConnectorPlaylistBrowseSchema[] = [];

export interface PickedPlaylist {
  id: string;
  name: string;
}

interface ConnectorPlaylistPickerDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The connector being browsed — drives the API call, title copy, + branding. */
  connector: ConnectorMetadataSchema;
  /**
   * Receives the selected playlists as `{id, name}` pairs. The browser
   * operates in external-ID space (provider identifiers), not internal
   * UUIDs — names tag along so the confirm dialog avoids a re-query.
   */
  onConfirm?: (playlists: PickedPlaylist[]) => void;
  /**
   * `import` (default) — multi-select with an Import action + per-row tag/rate
   * assignment menu, for the Playlists import flow. `select` — single-select,
   * click-a-row-to-confirm, with the import-only chrome stripped, for the
   * link-to-existing-playlist flow (emits exactly one playlist via onConfirm).
   */
  mode?: "import" | "select";
}

function PlaylistRowSkeleton() {
  return (
    <div className="flex items-center gap-3 py-2">
      <Skeleton className="size-4" />
      <Skeleton className="size-10 rounded-sm" />
      <div className="flex-1 space-y-1">
        <Skeleton className="h-4 w-48" />
        <Skeleton className="h-3 w-32" />
      </div>
      <Skeleton className="h-6 w-24 rounded-full" />
    </div>
  );
}

function FilterChip({
  label,
  selected,
  onClick,
}: {
  label: string;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "inline-flex items-center rounded-full border px-3 py-1 font-display text-xs font-medium transition-colors",
        selected
          ? "border-primary bg-primary/15 text-primary"
          : "border-border text-text-muted hover:bg-accent hover:text-text",
      )}
    >
      {label}
    </button>
  );
}

export function ConnectorPlaylistPickerDialog({
  open,
  onOpenChange,
  connector,
  onConfirm,
  mode = "import",
}: ConnectorPlaylistPickerDialogProps) {
  const isSelect = mode === "select";
  const { search, setSearch, deferredSearch, isSearching } = useTrackSearch();
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [attributeFilter, setAttributeFilter] =
    useState<AttributeFilter>("all");
  const [assignDialog, setAssignDialog] = useState<{
    mode: AssignMode;
    playlist: ConnectorPlaylistBrowseSchema;
  } | null>(null);

  const connectorName = connector.name;
  const connectorLabel = connector.display_name;

  const { data, isLoading, isError, error } =
    useListConnectorPlaylistsApiV1ConnectorsServicePlaylistsGet(
      connectorName,
      { force_refresh: false },
      { query: { enabled: open } },
    );

  const refresh = useMutation({
    mutationFn: () =>
      listConnectorPlaylistsApiV1ConnectorsServicePlaylistsGet(connectorName, {
        force_refresh: true,
      }),
    // The spinner covers the round trip, so the list must be current first.
    meta: {
      errorLabel: `Failed to refresh ${connectorLabel} playlists`,
      invalidates: ["connector-playlists"],
      awaitInvalidation: true,
    },
  });

  const response = data?.status === 200 ? data.data : undefined;
  const playlists: ConnectorPlaylistBrowseSchema[] =
    response?.data ?? NO_PLAYLISTS;

  const filtered = useMemo(() => {
    const needle = deferredSearch.trim().toLowerCase();
    return playlists.filter((p) => {
      if (needle && !p.name.toLowerCase().includes(needle)) return false;
      if (statusFilter !== "all" && p.import_status !== statusFilter)
        return false;
      if (attributeFilter === "collaborative" && !p.collaborative) return false;
      if (attributeFilter === "public" && !p.is_public) return false;
      return true;
    });
  }, [playlists, deferredSearch, statusFilter, attributeFilter]);

  // Selection is keyed on every known playlist and only scoped to the visible
  // rows for the header checkbox, so narrowing the search cannot silently drop
  // what the user already picked.
  const allIds = useMemo(
    () => playlists.map((p) => p.connector_playlist_identifier),
    [playlists],
  );
  const filteredIds = useMemo(
    () => filtered.map((p) => p.connector_playlist_identifier),
    [filtered],
  );
  const selection = useSelectionSet(allIds, { visibleIds: filteredIds });

  const handleOpenChange = (next: boolean) => {
    if (!next) {
      selection.clear();
      if (search) setSearch("");
      if (statusFilter !== "all") setStatusFilter("all");
      if (attributeFilter !== "all") setAttributeFilter("all");
    }
    onOpenChange(next);
  };

  const confirmSelection = () => {
    if (selection.size === 0) return;
    onConfirm?.(
      playlists
        .filter((p) => selection.isSelected(p.connector_playlist_identifier))
        .map((p) => ({ id: p.connector_playlist_identifier, name: p.name })),
    );
  };

  const confirmOne = (p: ConnectorPlaylistBrowseSchema) => {
    onConfirm?.([{ id: p.connector_playlist_identifier, name: p.name }]);
    // The parent closes us via the controlled prop, bypassing
    // handleOpenChange's reset — run it here so a reopen starts clean.
    handleOpenChange(false);
  };

  return (
    <>
      <ResponsiveDialog
        open={open}
        onOpenChange={handleOpenChange}
        className="sm:max-w-2xl"
      >
        <DialogHeader>
          <div className="flex items-start justify-between gap-3">
            <div>
              <DialogTitle>
                {isSelect
                  ? `Select a ${connectorLabel} playlist`
                  : `Import from ${connectorLabel}`}
              </DialogTitle>
              <p className="mt-1 text-sm text-text-muted">
                {response?.from_cache
                  ? `Showing cached playlists. Refresh to pull the latest from ${connectorLabel}.`
                  : `Latest from ${connectorLabel}.`}
              </p>
            </div>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => refresh.mutate()}
              disabled={refresh.isPending}
              aria-label={`Refresh from ${connectorLabel}`}
            >
              {refresh.isPending ? (
                <Loader2 className="animate-spin" />
              ) : (
                <RefreshCw />
              )}
              Refresh
            </Button>
          </div>
        </DialogHeader>

        <div className="space-y-3">
          <div className="relative">
            <Search className="absolute top-1/2 left-3 size-4 -translate-y-1/2 text-text-muted" />
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search playlists…"
              className="pl-9"
              aria-label={`Search ${connectorLabel} playlists`}
            />
          </div>

          <div className="flex flex-wrap items-center gap-2">
            {STATUS_FILTERS.map(([value, label]) => (
              <FilterChip
                key={value}
                label={label}
                selected={statusFilter === value}
                onClick={() => setStatusFilter(value)}
              />
            ))}
            <span className="mx-1 h-4 w-px bg-border" aria-hidden="true" />
            {ATTRIBUTE_FILTERS.map(([value, label]) => (
              <FilterChip
                key={value}
                label={label}
                selected={attributeFilter === value}
                onClick={() => setAttributeFilter(value)}
              />
            ))}
          </div>
        </div>

        <div className="max-h-[50vh] overflow-y-auto rounded-md border">
          <QueryStates
            loading={isLoading}
            isError={isError}
            error={error}
            errorHeading={`Couldn't load ${connectorLabel} playlists`}
            skeleton={
              <div className="px-3 py-2">
                {Array.from({ length: 5 }).map((_, i) => (
                  // biome-ignore lint/suspicious/noArrayIndexKey: static skeleton
                  <PlaylistRowSkeleton key={i} />
                ))}
              </div>
            }
            isEmpty={filtered.length === 0}
            empty={
              <EmptyState
                heading={
                  playlists.length === 0
                    ? "No playlists"
                    : "No playlists match your filters"
                }
                description={
                  playlists.length === 0
                    ? `Connect ${connectorLabel} or create a playlist there to see it here.`
                    : "Try removing a filter or clearing the search."
                }
              />
            }
          >
            <div>
              {!isSelect && (
                <div className="sticky top-0 z-10 flex items-center gap-3 border-b bg-background px-3 py-2 text-xs text-text-muted">
                  <Checkbox
                    checked={selection.headerChecked}
                    onCheckedChange={() => selection.toggleAll()}
                    aria-label="Select all visible playlists"
                  />
                  <span>
                    {selection.visibleSize} of {filtered.length} selected
                    {selection.size > selection.visibleSize &&
                      ` · ${selection.size} in total`}
                    {isSearching && " · filtering…"}
                  </span>
                </div>
              )}
              {filtered.map((p) => {
                const id = p.connector_playlist_identifier;
                return isSelect ? (
                  <ConnectorPlaylistSelectRow
                    key={id}
                    playlist={p}
                    onSelect={confirmOne}
                  />
                ) : (
                  <ConnectorPlaylistImportRow
                    key={id}
                    playlist={p}
                    connectorName={connectorName}
                    selected={selection.isSelected(id)}
                    onSelectedChange={(next) => selection.toggle(id, next)}
                    onAssign={(assignMode, playlist) =>
                      setAssignDialog({ mode: assignMode, playlist })
                    }
                  />
                );
              })}
            </div>
          </QueryStates>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => handleOpenChange(false)}>
            Cancel
          </Button>
          {!isSelect && (
            <Button disabled={selection.size === 0} onClick={confirmSelection}>
              Import {pluralize(selection.size, "playlist")}
            </Button>
          )}
        </DialogFooter>
      </ResponsiveDialog>
      {assignDialog && (
        <AssignPlaylistDialog
          open
          onOpenChange={(next) => {
            if (!next) setAssignDialog(null);
          }}
          mode={assignDialog.mode}
          connector={connector}
          playlist={assignDialog.playlist}
        />
      )}
    </>
  );
}
