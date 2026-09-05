/**
 * Picker for a `task_ref` config field: one of the selected node's upstream
 * tasks. Disabled with an inline hint until the node has an incoming edge.
 */

import type { ConfigFieldSchema } from "#/api/generated/model";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "#/components/ui/select";
import type { UpstreamRef } from "#/stores/editor-store";

export const NO_UPSTREAM_MESSAGE = "Connect an upstream node first";

export interface TaskRefInputProps {
  field: ConfigFieldSchema;
  value: unknown;
  upstreams: readonly UpstreamRef[];
  onChange: (key: string, value: unknown) => void;
  onBlur: (key: string) => void;
  fieldId: string;
  hasError: boolean;
  errorId: string;
}

export function TaskRefInput({
  field,
  value,
  upstreams,
  onChange,
  onBlur,
  fieldId,
  hasError,
  errorId,
}: TaskRefInputProps) {
  const current = typeof value === "string" ? value : "";
  const disabled = upstreams.length === 0;
  // A saved value whose edge was removed stays visible so the user can see
  // what is wrong; validation flags it.
  const stale = current && !upstreams.some((u) => u.id === current);

  return (
    <Select
      value={current}
      disabled={disabled}
      onValueChange={(v) => {
        onChange(field.key, v);
        onBlur(field.key);
      }}
    >
      <SelectTrigger
        id={fieldId}
        className={`h-8 w-full font-display text-xs ${
          hasError ? "border-destructive/50" : ""
        }`}
        aria-invalid={hasError || undefined}
        aria-describedby={hasError ? errorId : undefined}
      >
        <SelectValue
          placeholder={disabled ? NO_UPSTREAM_MESSAGE : "Select..."}
        />
      </SelectTrigger>
      <SelectContent>
        {upstreams.map((u) => (
          <SelectItem key={u.id} value={u.id}>
            <span className="font-display text-xs">
              {u.label}{" "}
              <span className="font-mono text-[10px] text-text-faint">
                ({u.id})
              </span>
            </span>
          </SelectItem>
        ))}
        {stale && (
          <SelectItem value={current} disabled>
            <span className="font-mono text-xs">{current} (not connected)</span>
          </SelectItem>
        )}
      </SelectContent>
    </Select>
  );
}
