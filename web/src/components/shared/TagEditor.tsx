import { TagAutocomplete } from "./TagAutocomplete";
import { TagChip } from "./TagChip";

interface TagEditorProps {
  /** Current tags on the track (already-normalized). */
  value: string[];
  onAdd: (rawTag: string) => void;
  onRemove: (tag: string) => void;
  disabled?: boolean;
}

export function TagEditor({
  value,
  onAdd,
  onRemove,
  disabled = false,
}: TagEditorProps) {
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-2">
      {value.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {value.map((tag) => (
            <TagChip
              key={tag}
              tag={tag}
              onRemove={disabled ? undefined : () => onRemove(tag)}
            />
          ))}
        </div>
      )}
      {!disabled && (
        <TagAutocomplete exclude={value} onAdd={onAdd} className="max-w-md" />
      )}
    </div>
  );
}
