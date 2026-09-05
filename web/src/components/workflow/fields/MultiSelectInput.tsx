/**
 * Checkbox group for a `multi_select` config field. The value is `string[]`
 * over `field.options`; while the config key is absent the group shows
 * `field.default` and writes nothing until the user toggles a box. Emptying
 * the selection emits `undefined` so the key is dropped from config.
 */

import type { ConfigFieldSchema } from "#/api/generated/model";
import { Checkbox } from "#/components/ui/checkbox";

export interface MultiSelectInputProps {
  field: ConfigFieldSchema;
  value: unknown;
  onChange: (key: string, value: unknown) => void;
  onBlur: (key: string) => void;
  fieldId: string;
  hasError: boolean;
  errorId: string;
}

/** The selection the group displays: config value, else declared default. */
export function resolveMultiSelectValue(
  field: ConfigFieldSchema,
  value: unknown,
): string[] {
  if (Array.isArray(value)) return value.filter((v) => typeof v === "string");
  return Array.isArray(field.default) ? field.default : [];
}

export function MultiSelectInput({
  field,
  value,
  onChange,
  onBlur,
  fieldId,
  hasError,
  errorId,
}: MultiSelectInputProps) {
  const selected = new Set(resolveMultiSelectValue(field, value));
  const options = field.options ?? [];

  const toggle = (optionValue: string, checked: boolean) => {
    const next = new Set(selected);
    if (checked) {
      next.add(optionValue);
    } else {
      next.delete(optionValue);
    }
    // Emit in option order so the stored array is canonical.
    const ordered = options.map((o) => o.value).filter((v) => next.has(v));
    onChange(field.key, ordered.length > 0 ? ordered : undefined);
    onBlur(field.key);
  };

  return (
    <fieldset
      id={fieldId}
      aria-labelledby={`${fieldId}-label`}
      aria-invalid={hasError || undefined}
      aria-describedby={hasError ? errorId : undefined}
      className={`min-w-0 space-y-1.5 rounded-md border px-2.5 py-2 ${
        hasError ? "border-destructive/50" : "border-transparent"
      }`}
    >
      {options.map((opt) => {
        const optionId = `${fieldId}-${opt.value}`;
        return (
          <div key={opt.value} className="flex items-start gap-2">
            <Checkbox
              id={optionId}
              className="mt-0.5"
              checked={selected.has(opt.value)}
              onCheckedChange={(c) => toggle(opt.value, c === true)}
            />
            <label htmlFor={optionId} className="cursor-pointer">
              <span className="block font-display text-xs text-text">
                {opt.label}
              </span>
              {opt.description && (
                <span className="block font-body text-[10px] text-text-faint">
                  {opt.description}
                </span>
              )}
            </label>
          </div>
        );
      })}
    </fieldset>
  );
}
