/**
 * Rules that depend on a config field's declared type (`ConfigFieldSchema`).
 * Every consumer of node config — panel, validation, editor store — goes
 * through these so a rule lives in one place.
 */

import type { ConfigFieldSchema } from "#/api/generated/model";

const LEGACY_BOOLEANS: Readonly<Record<string, boolean>> = {
  true: true,
  false: false,
};

/**
 * Read `value` as the field's declared type.
 *
 * Boolean fields were once `select`s that persisted the strings "true" /
 * "false"; those map to real booleans and any other string reads as unset so
 * the declared default applies — the same reading the backend's `cfg_bool`
 * gives them. Every other field type passes through untouched.
 */
export function coerceFieldValue(
  field: ConfigFieldSchema,
  value: unknown,
): unknown {
  if (field.field_type === "boolean" && typeof value === "string") {
    return LEGACY_BOOLEANS[value.trim().toLowerCase()];
  }
  return value;
}

/** Keys of the fields whose value names one of the node's upstream tasks. */
export function taskRefKeys(schema: readonly ConfigFieldSchema[]): string[] {
  return schema.filter((f) => f.field_type === "task_ref").map((f) => f.key);
}
