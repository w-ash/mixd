import { useQueryClient } from "@tanstack/react-query";
import {
  GitMerge,
  Pencil,
  Sparkles,
  Tag as TagIcon,
  Trash2,
} from "lucide-react";
import { useDeferredValue, useState } from "react";
import type {
  HTTPValidationError,
  TagOperationResult,
  TagSummarySchema,
} from "#/api/generated/model";
import {
  getListTagsApiV1TagsGetQueryKey,
  useDeleteTagApiV1TagsTagDelete,
  useListTagsApiV1TagsGet,
  useMergeTagsApiV1TagsMergePost,
  useRenameTagApiV1TagsTagPatch,
} from "#/api/generated/tags/tags";
import { PageHeader } from "#/components/layout/PageHeader";
import { BulkApplyAssignmentsDialog } from "#/components/shared/BulkApplyAssignmentsDialog";
import { ConfirmationDialog } from "#/components/shared/ConfirmationDialog";
import { EmptyState } from "#/components/shared/EmptyState";
import { Button } from "#/components/ui/button";
import { Input } from "#/components/ui/input";
import { Skeleton } from "#/components/ui/skeleton";
import { formatDate } from "#/lib/format";
import { toasts } from "#/lib/toasts";

type DialogMode = "rename" | "merge" | "delete";

interface ActiveDialog {
  mode: DialogMode;
  tag: TagSummarySchema;
}

function TagsSkeleton() {
  return (
    <div className="space-y-3">
      {Array.from({ length: 6 }).map((_, i) => (
        <div
          // biome-ignore lint/suspicious/noArrayIndexKey: static skeleton
          key={i}
          className="flex items-center gap-4 rounded-lg border border-border bg-surface-elevated px-4 py-3"
        >
          <Skeleton className="h-4 w-32" />
          <Skeleton className="h-3 w-20" />
          <div className="flex-1" />
          <Skeleton className="h-3 w-16" />
          <Skeleton className="h-7 w-24" />
        </div>
      ))}
    </div>
  );
}

export function Tags() {
  const [search, setSearch] = useState("");
  const [activeDialog, setActiveDialog] = useState<ActiveDialog | null>(null);
  const [targetInput, setTargetInput] = useState("");
  const [bulkApplyOpen, setBulkApplyOpen] = useState(false);

  const queryClient = useQueryClient();
  const deferredSearch = useDeferredValue(search);
  const trimmedSearch = deferredSearch.trim();

  const { data, isPending, isError } = useListTagsApiV1TagsGet({
    q: trimmedSearch || undefined,
    limit: 500,
  });
  const tags = data?.status === 200 ? data.data : [];

  const invalidateTags = () =>
    queryClient.invalidateQueries({
      queryKey: getListTagsApiV1TagsGetQueryKey(),
    });

  const closeDialog = () => {
    setActiveDialog(null);
    setTargetInput("");
  };

  const renameMutation = useRenameTagApiV1TagsTagPatch();
  const deleteMutation = useDeleteTagApiV1TagsTagDelete();
  const mergeMutation = useMergeTagsApiV1TagsMergePost();

  // Tag-management mutations all share the same response envelope shape
  // (200 → TagOperationResult, 422 → HTTPValidationError) and the same
  // success-flow obligations (toast, invalidate, close). The factory keeps
  // each handler down to "what's different": label + success template.
  const tagMutationCallbacks = (
    successMessage: (count: number) => string,
    errorLabel: string,
  ) => ({
    onSuccess: (resp: {
      status: number;
      data: TagOperationResult | HTTPValidationError;
    }) => {
      if (resp.status !== 200) {
        toasts.error(errorLabel, resp.data);
        return;
      }
      toasts.success(
        successMessage((resp.data as TagOperationResult).affected_count),
      );
      invalidateTags();
      closeDialog();
    },
    onError: (err: unknown) => toasts.error(errorLabel, err),
  });

  const handleRename = () => {
    if (!activeDialog || activeDialog.mode !== "rename") return;
    const newTag = targetInput.trim();
    if (!newTag) return;
    const sourceTag = activeDialog.tag.tag;
    renameMutation.mutate(
      { tag: sourceTag, data: { new_tag: newTag } },
      tagMutationCallbacks(
        (count) =>
          `Renamed "${sourceTag}" → "${newTag}" across ${count} tracks`,
        "Rename failed",
      ),
    );
  };

  const handleMerge = () => {
    if (!activeDialog || activeDialog.mode !== "merge") return;
    const target = targetInput.trim();
    if (!target) return;
    const sourceTag = activeDialog.tag.tag;
    mergeMutation.mutate(
      { data: { source: sourceTag, target } },
      tagMutationCallbacks(
        (count) =>
          `Merged "${sourceTag}" into "${target}" across ${count} tracks`,
        "Merge failed",
      ),
    );
  };

  const handleDelete = () => {
    if (!activeDialog || activeDialog.mode !== "delete") return;
    const sourceTag = activeDialog.tag.tag;
    deleteMutation.mutate(
      { tag: sourceTag },
      tagMutationCallbacks(
        (count) => `Deleted "${sourceTag}" from ${count} tracks`,
        "Delete failed",
      ),
    );
  };

  const isMutating =
    renameMutation.isPending ||
    deleteMutation.isPending ||
    mergeMutation.isPending;

  return (
    <div>
      <title>Tags — Mixd</title>
      <PageHeader
        title="Tags"
        description="Maintain your tag taxonomy. Rename, merge, or delete tags across every track that carries them."
        action={
          <Button
            variant="outline"
            size="sm"
            onClick={() => setBulkApplyOpen(true)}
          >
            <Sparkles className="size-3.5" aria-hidden />
            Apply all assignments
          </Button>
        }
      />

      <BulkApplyAssignmentsDialog
        open={bulkApplyOpen}
        onOpenChange={setBulkApplyOpen}
      />

      <div className="mb-6">
        <Input
          type="search"
          placeholder="Filter tags…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          aria-label="Filter tags"
          className="max-w-sm"
        />
      </div>

      {isPending && <TagsSkeleton />}

      {isError && (
        <EmptyState
          heading="Couldn't load tags"
          description="Refresh the page or check your connection."
          role="alert"
        />
      )}

      {!isPending && !isError && tags.length === 0 && (
        <EmptyState
          icon={<TagIcon className="size-8" aria-hidden />}
          heading={
            trimmedSearch
              ? `No tags matching "${trimmedSearch}"`
              : "No tags yet"
          }
          description={
            trimmedSearch
              ? "Try a different search."
              : "Tag tracks from the Library or a Track Detail page to start building your taxonomy."
          }
        />
      )}

      {!isPending && !isError && tags.length > 0 && (
        <div className="overflow-hidden rounded-lg border border-border bg-surface-elevated shadow-elevated">
          <table className="w-full">
            <thead>
              <tr className="border-b border-border bg-surface-sunken text-left">
                <th className="px-4 py-2.5 font-display text-xs font-medium uppercase tracking-wider text-text-muted">
                  Tag
                </th>
                <th className="px-4 py-2.5 font-display text-xs font-medium uppercase tracking-wider text-text-muted">
                  Namespace
                </th>
                <th className="px-4 py-2.5 text-right font-display text-xs font-medium uppercase tracking-wider text-text-muted">
                  Tracks
                </th>
                <th className="px-4 py-2.5 font-display text-xs font-medium uppercase tracking-wider text-text-muted">
                  Last used
                </th>
                <th className="px-4 py-2.5 text-right font-display text-xs font-medium uppercase tracking-wider text-text-muted">
                  Actions
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {tags.map((tag) => (
                <tr key={tag.tag} className="hover:bg-surface-sunken">
                  <td className="px-4 py-3 font-mono text-sm text-text">
                    {tag.tag}
                  </td>
                  <td className="px-4 py-3 font-mono text-xs text-text-muted">
                    {tag.namespace ?? "—"}
                  </td>
                  <td className="px-4 py-3 text-right font-mono text-sm text-text">
                    {tag.track_count}
                  </td>
                  <td className="px-4 py-3 font-mono text-xs text-text-muted">
                    {formatDate(tag.last_used_at)}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <div className="flex justify-end gap-1">
                      <Button
                        variant="ghost"
                        size="sm"
                        aria-label={`Rename ${tag.tag}`}
                        onClick={() => {
                          setTargetInput(tag.tag);
                          setActiveDialog({ mode: "rename", tag });
                        }}
                      >
                        <Pencil className="size-3.5" aria-hidden />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        aria-label={`Merge ${tag.tag} into another tag`}
                        onClick={() => {
                          setTargetInput("");
                          setActiveDialog({ mode: "merge", tag });
                        }}
                      >
                        <GitMerge className="size-3.5" aria-hidden />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        aria-label={`Delete ${tag.tag}`}
                        onClick={() => setActiveDialog({ mode: "delete", tag })}
                      >
                        <Trash2 className="size-3.5" aria-hidden />
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Rename dialog */}
      <ConfirmationDialog
        open={activeDialog?.mode === "rename"}
        onOpenChange={(open) => !open && closeDialog()}
        title="Rename tag"
        description={
          activeDialog?.mode === "rename"
            ? `"${activeDialog.tag.tag}" appears on ${activeDialog.tag.track_count} ${activeDialog.tag.track_count === 1 ? "track" : "tracks"}. The new name will replace it everywhere.`
            : undefined
        }
        confirmLabel={`Rename across ${activeDialog?.mode === "rename" ? activeDialog.tag.track_count : 0} tracks`}
        isPending={renameMutation.isPending}
        disabled={!targetInput.trim()}
        onConfirm={handleRename}
      >
        <Input
          autoFocus
          value={targetInput}
          onChange={(e) => setTargetInput(e.target.value)}
          placeholder="mood:ambient"
          aria-label="New tag name"
        />
      </ConfirmationDialog>

      {/* Merge dialog */}
      <ConfirmationDialog
        open={activeDialog?.mode === "merge"}
        onOpenChange={(open) => !open && closeDialog()}
        title="Merge into another tag"
        description={
          activeDialog?.mode === "merge"
            ? `Every track currently tagged "${activeDialog.tag.tag}" will be moved to the target tag. Tracks already on the target stay as-is.`
            : undefined
        }
        confirmLabel={`Merge ${activeDialog?.mode === "merge" ? activeDialog.tag.track_count : 0} tracks`}
        isPending={mergeMutation.isPending}
        disabled={!targetInput.trim()}
        onConfirm={handleMerge}
      >
        <Input
          autoFocus
          value={targetInput}
          onChange={(e) => setTargetInput(e.target.value)}
          placeholder="Target tag (e.g. context:workout)"
          aria-label="Target tag"
        />
      </ConfirmationDialog>

      {/* Delete dialog */}
      <ConfirmationDialog
        open={activeDialog?.mode === "delete"}
        onOpenChange={(open) => !open && closeDialog()}
        title="Delete tag"
        description={
          activeDialog?.mode === "delete"
            ? `Removes "${activeDialog.tag.tag}" from ${activeDialog.tag.track_count} ${activeDialog.tag.track_count === 1 ? "track" : "tracks"} and clears its history. This can't be undone.`
            : undefined
        }
        confirmLabel={`Delete from ${activeDialog?.mode === "delete" ? activeDialog.tag.track_count : 0} tracks`}
        destructive
        isPending={deleteMutation.isPending}
        onConfirm={handleDelete}
      />

      {/* Lightweight overlay while any mutation is in flight (in case dialog
          closes before completion). */}
      {isMutating && (
        <span className="sr-only" role="status">
          Updating tags…
        </span>
      )}
    </div>
  );
}
