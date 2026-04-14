import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, RefreshCw, Search } from "lucide-react";
import { useMemo, useState } from "react";

import {
  getListSpotifyPlaylistsApiV1ConnectorsSpotifyPlaylistsGetQueryKey,
  listSpotifyPlaylistsApiV1ConnectorsSpotifyPlaylistsGet,
  useListSpotifyPlaylistsApiV1ConnectorsSpotifyPlaylistsGet,
} from "#/api/generated/connectors/connectors";
import type { SpotifyPlaylistBrowseSchema } from "#/api/generated/model";
import { Button } from "#/components/ui/button";
import { Checkbox } from "#/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "#/components/ui/dialog";
import { Input } from "#/components/ui/input";
import { Skeleton } from "#/components/ui/skeleton";
import { useTrackSearch } from "#/hooks/useTrackSearch";
import { cn } from "#/lib/utils";

import { EmptyState } from "./EmptyState";
import { ImportStatusPill } from "./ImportStatusPill";
import { QueryErrorState } from "./QueryErrorState";

/**
 * On-demand picker: opened contextually from action buttons (Playlists
 * page today, tag-mapping flows in v0.7.4). Not a persistent route.
 *
 * Cache-first list + client-side filter (Spotify has no name search on
 * /me/playlists). "Refresh" forces a fetch + cache upsert. Selection is
 * scoped to the dialog — resets on close — and emits via onConfirm.
 */

type StatusFilter = "all" | "not_imported" | "imported";
type AttributeFilter = "all" | "collaborative" | "public";

export interface PickedPlaylist {
  id: string;
  name: string;
}

interface SpotifyPlaylistPickerDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /**
   * Receives the selected playlists as `{id, name}` pairs. The browser
   * operates in external-ID space (Spotify identifiers), not internal
   * UUIDs — names tag along so the confirm dialog avoids a re-query.
   */
  onConfirm?: (playlists: PickedPlaylist[]) => void;
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

export function SpotifyPlaylistPickerDialog({
  open,
  onOpenChange,
  onConfirm,
}: SpotifyPlaylistPickerDialogProps) {
  const queryClient = useQueryClient();
  const { search, setSearch, deferredSearch, isSearching } = useTrackSearch();
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [attributeFilter, setAttributeFilter] =
    useState<AttributeFilter>("all");

  const { data, isLoading, isError, error } =
    useListSpotifyPlaylistsApiV1ConnectorsSpotifyPlaylistsGet(
      { force_refresh: false },
      { query: { enabled: open } },
    );

  const refresh = useMutation({
    mutationFn: () =>
      listSpotifyPlaylistsApiV1ConnectorsSpotifyPlaylistsGet({
        force_refresh: true,
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey:
          getListSpotifyPlaylistsApiV1ConnectorsSpotifyPlaylistsGetQueryKey(),
      });
    },
    meta: { errorLabel: "Failed to refresh Spotify playlists" },
  });

  const response = data?.status === 200 ? data.data : undefined;
  const playlists: SpotifyPlaylistBrowseSchema[] = response?.data ?? [];

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

  const { visibleIds, visibleSelectedCount } = useMemo(() => {
    const ids = filtered.map((p) => p.connector_playlist_identifier);
    let count = 0;
    for (const id of ids) if (selectedIds.has(id)) count++;
    return { visibleIds: ids, visibleSelectedCount: count };
  }, [filtered, selectedIds]);

  const headerChecked: boolean | "indeterminate" =
    filtered.length === 0
      ? false
      : visibleSelectedCount === filtered.length
        ? true
        : visibleSelectedCount > 0
          ? "indeterminate"
          : false;

  const toggleHeader = (checked: boolean | "indeterminate") => {
    const turnOn = checked === true;
    setSelectedIds((prev) => {
      // Skip allocating a new Set when the toggle is a no-op — Radix
      // re-fires onCheckedChange even when state hasn't drifted.
      if (turnOn && visibleIds.every((id) => prev.has(id))) return prev;
      if (!turnOn && visibleIds.every((id) => !prev.has(id))) return prev;
      const next = new Set(prev);
      for (const id of visibleIds) {
        if (turnOn) next.add(id);
        else next.delete(id);
      }
      return next;
    });
  };

  const toggleRow = (id: string, checked: boolean) => {
    setSelectedIds((prev) => {
      if (checked && prev.has(id)) return prev;
      if (!checked && !prev.has(id)) return prev;
      const next = new Set(prev);
      if (checked) next.add(id);
      else next.delete(id);
      return next;
    });
  };

  const handleOpenChange = (next: boolean) => {
    if (!next) {
      if (selectedIds.size > 0) setSelectedIds(new Set());
      if (search) setSearch("");
      if (statusFilter !== "all") setStatusFilter("all");
      if (attributeFilter !== "all") setAttributeFilter("all");
    }
    onOpenChange(next);
  };

  const selectedCount = selectedIds.size;

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <div className="flex items-start justify-between gap-3">
            <div>
              <DialogTitle>Import from Spotify</DialogTitle>
              <p className="mt-1 text-sm text-text-muted">
                {response?.from_cache
                  ? "Showing cached playlists. Refresh to pull the latest from Spotify."
                  : "Latest from Spotify."}
              </p>
            </div>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => refresh.mutate()}
              disabled={refresh.isPending}
              aria-label="Refresh from Spotify"
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
              aria-label="Search Spotify playlists"
            />
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <FilterChip
              label="All"
              selected={statusFilter === "all"}
              onClick={() => setStatusFilter("all")}
            />
            <FilterChip
              label="Not imported"
              selected={statusFilter === "not_imported"}
              onClick={() => setStatusFilter("not_imported")}
            />
            <FilterChip
              label="Imported"
              selected={statusFilter === "imported"}
              onClick={() => setStatusFilter("imported")}
            />
            <span className="mx-1 h-4 w-px bg-border" aria-hidden="true" />
            <FilterChip
              label="Any kind"
              selected={attributeFilter === "all"}
              onClick={() => setAttributeFilter("all")}
            />
            <FilterChip
              label="Collaborative"
              selected={attributeFilter === "collaborative"}
              onClick={() => setAttributeFilter("collaborative")}
            />
            <FilterChip
              label="Public"
              selected={attributeFilter === "public"}
              onClick={() => setAttributeFilter("public")}
            />
          </div>
        </div>

        <div className="max-h-[50vh] overflow-y-auto rounded-md border">
          {isLoading ? (
            <div className="px-3 py-2">
              {Array.from({ length: 5 }).map((_, i) => (
                // biome-ignore lint/suspicious/noArrayIndexKey: static skeleton
                <PlaylistRowSkeleton key={i} />
              ))}
            </div>
          ) : isError ? (
            <div className="p-4">
              <QueryErrorState
                error={error}
                heading="Couldn't load Spotify playlists"
              />
            </div>
          ) : filtered.length === 0 ? (
            <EmptyState
              heading={
                playlists.length === 0
                  ? "No playlists"
                  : "No playlists match your filters"
              }
              description={
                playlists.length === 0
                  ? "Connect Spotify or create a playlist there to see it here."
                  : "Try removing a filter or clearing the search."
              }
            />
          ) : (
            <div>
              <div className="sticky top-0 z-10 flex items-center gap-3 border-b bg-background px-3 py-2 text-xs text-text-muted">
                <Checkbox
                  checked={headerChecked}
                  onCheckedChange={toggleHeader}
                  aria-label="Select all visible playlists"
                />
                <span>
                  {visibleSelectedCount} of {filtered.length} selected
                  {isSearching && " · filtering…"}
                </span>
              </div>
              {filtered.map((p) => {
                const id = p.connector_playlist_identifier;
                const checked = selectedIds.has(id);
                const rowId = `spotify-pick-${id}`;
                return (
                  <div
                    key={id}
                    className="flex items-center gap-3 border-b px-3 py-2 last:border-b-0 hover:bg-accent/30"
                  >
                    <Checkbox
                      id={rowId}
                      checked={checked}
                      onCheckedChange={(next) => toggleRow(id, next === true)}
                    />
                    {p.image_url ? (
                      <img
                        src={p.image_url}
                        alt=""
                        className="size-10 rounded-sm object-cover"
                        loading="lazy"
                      />
                    ) : (
                      <div
                        className="size-10 shrink-0 rounded-sm bg-surface-muted"
                        aria-hidden="true"
                      />
                    )}
                    <label
                      htmlFor={rowId}
                      className="min-w-0 flex-1 cursor-pointer"
                    >
                      <p className="truncate font-medium text-text">{p.name}</p>
                      <p className="truncate text-xs text-text-muted">
                        {p.owner ?? "Unknown"} ·{" "}
                        <span className="tabular-nums">
                          {p.track_count.toLocaleString()}
                        </span>{" "}
                        tracks
                      </p>
                    </label>
                    <ImportStatusPill status={p.import_status} />
                  </div>
                );
              })}
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => handleOpenChange(false)}>
            Cancel
          </Button>
          <Button
            disabled={selectedCount === 0}
            onClick={() => {
              if (selectedCount === 0) return;
              const picked: PickedPlaylist[] = [];
              for (const p of playlists) {
                if (selectedIds.has(p.connector_playlist_identifier)) {
                  picked.push({
                    id: p.connector_playlist_identifier,
                    name: p.name,
                  });
                }
              }
              onConfirm?.(picked);
            }}
          >
            Import {selectedCount} playlist{selectedCount === 1 ? "" : "s"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
