import { useState } from "react";

import { Button } from "#/components/ui/button";
import { cn } from "#/lib/utils";

import { TagAutocomplete } from "./TagAutocomplete";
import { TagChip } from "./TagChip";

type TagMode = "and" | "or";

interface TagFilterProps {
  tags: string[];
  mode: TagMode;
  onTagsChange: (tags: string[]) => void;
  onModeChange: (mode: TagMode) => void;
}

export function TagFilter({
  tags,
  mode,
  onTagsChange,
  onModeChange,
}: TagFilterProps) {
  const [isAdding, setIsAdding] = useState(false);

  const handleAdd = (rawTag: string) => {
    // Server's normalize_tag is authoritative (the /tracks route normalizes
    // ?tag= before filtering). Forward as-typed so partial client-side rules
    // can't drift from the server.
    const value = rawTag.trim();
    if (!value || tags.includes(value)) {
      setIsAdding(false);
      return;
    }
    onTagsChange([...tags, value]);
    setIsAdding(false);
  };

  return (
    <div className="flex flex-wrap items-center gap-2">
      {tags.map((tag) => (
        <TagChip
          key={tag}
          tag={tag}
          onRemove={() => onTagsChange(tags.filter((t) => t !== tag))}
        />
      ))}

      {isAdding ? (
        <TagAutocomplete
          exclude={tags}
          onAdd={handleAdd}
          autoFocus
          placeholder="Filter by tag…"
          className="w-56"
        />
      ) : (
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => setIsAdding(true)}
          aria-label="Add tag filter"
        >
          {tags.length === 0 ? "Filter by tag…" : "+ tag"}
        </Button>
      )}

      {tags.length >= 2 && (
        <fieldset className="inline-flex overflow-hidden rounded-md border border-border-muted p-0 text-xs">
          <legend className="sr-only">Tag filter mode</legend>
          <button
            type="button"
            onClick={() => onModeChange("and")}
            className={cn(
              "px-2 py-1 font-display uppercase tracking-wider transition-colors",
              mode === "and"
                ? "bg-primary text-primary-foreground"
                : "text-text-muted hover:bg-surface-sunken",
            )}
            aria-pressed={mode === "and"}
          >
            All
          </button>
          <button
            type="button"
            onClick={() => onModeChange("or")}
            className={cn(
              "border-l border-border-muted px-2 py-1 font-display uppercase tracking-wider transition-colors",
              mode === "or"
                ? "bg-primary text-primary-foreground"
                : "text-text-muted hover:bg-surface-sunken",
            )}
            aria-pressed={mode === "or"}
          >
            Any
          </button>
        </fieldset>
      )}
    </div>
  );
}
