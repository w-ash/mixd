import { Command } from "cmdk";
import { Plus, Tag } from "lucide-react";
import { useDeferredValue, useEffect, useRef, useState } from "react";

import { useListTagsApiV1TagsGet } from "#/api/generated/tags/tags";
import { cn } from "#/lib/utils";

interface TagAutocompleteProps {
  /** Tags already attached to the track — hidden from suggestions. */
  exclude?: string[];
  /** Called with the raw user input; caller normalizes + validates. */
  onAdd: (rawTag: string) => void;
  placeholder?: string;
  autoFocus?: boolean;
  className?: string;
}

const SUGGESTION_LIMIT = 20;

export function TagAutocomplete({
  exclude = [],
  onAdd,
  placeholder = "Add a tag…",
  autoFocus = false,
  className,
}: TagAutocompleteProps) {
  const [search, setSearch] = useState("");
  const deferredSearch = useDeferredValue(search);
  const inputRef = useRef<HTMLInputElement>(null);

  const { data } = useListTagsApiV1TagsGet({
    q: deferredSearch.trim() || undefined,
    limit: SUGGESTION_LIMIT,
  });

  const excludeSet = new Set(exclude);
  const suggestions =
    data?.status === 200 ? data.data.filter((t) => !excludeSet.has(t.tag)) : [];

  const trimmed = search.trim();
  const trimmedLower = trimmed.toLowerCase();
  // Server-side normalize_tag validates on submit, so we don't predict the
  // canonical form here — just hide the "Add" row when the typed value is
  // already an attached tag or already offered as a suggestion.
  const showAddNew =
    trimmed.length > 0 &&
    !excludeSet.has(trimmedLower) &&
    !suggestions.some((s) => s.tag === trimmedLower);

  useEffect(() => {
    if (autoFocus) {
      inputRef.current?.focus();
    }
  }, [autoFocus]);

  const submit = (value: string) => {
    if (!value.trim()) return;
    onAdd(value);
    setSearch("");
    inputRef.current?.focus();
  };

  return (
    <Command
      className={cn(
        "rounded-md border border-border-muted bg-surface",
        className,
      )}
      shouldFilter={false}
    >
      <div className="flex items-center gap-2 border-b border-border-muted px-3">
        <Tag className="size-4 text-text-muted" />
        <Command.Input
          ref={inputRef}
          value={search}
          onValueChange={setSearch}
          placeholder={placeholder}
          className="flex h-9 w-full bg-transparent font-mono text-sm text-text outline-none placeholder:text-text-faint"
        />
      </div>
      <Command.List className="max-h-56 overflow-y-auto p-1">
        {!trimmed && suggestions.length === 0 && (
          <Command.Empty className="p-3 text-center text-xs text-text-muted">
            Type to add or search your tags.
          </Command.Empty>
        )}

        {showAddNew && (
          <Command.Item
            key="__add_new"
            value={`__add_new_${trimmed}`}
            onSelect={() => submit(trimmed)}
            className="flex cursor-pointer items-center gap-2 rounded-md px-3 py-2 text-sm hover:bg-surface-sunken aria-selected:bg-surface-sunken"
          >
            <Plus className="size-3.5 text-text-muted" />
            <span className="text-text-muted">Add</span>
            <span className="font-mono text-text">{trimmed}</span>
          </Command.Item>
        )}

        {suggestions.map((s) => (
          <Command.Item
            key={s.tag}
            value={s.tag}
            onSelect={() => submit(s.tag)}
            className="flex cursor-pointer items-center justify-between gap-3 rounded-md px-3 py-2 text-sm hover:bg-surface-sunken aria-selected:bg-surface-sunken"
          >
            <span className="truncate font-mono text-text">{s.tag}</span>
            <span className="shrink-0 font-mono text-xs text-text-muted">
              {s.track_count}
            </span>
          </Command.Item>
        ))}
      </Command.List>
    </Command>
  );
}
