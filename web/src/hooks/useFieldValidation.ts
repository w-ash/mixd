/**
 * "Reward early, punish late" field validation hook.
 *
 * - Before first save attempt: only validates on blur if field was previously errored
 * - After save attempt: validates all required fields, shows errors immediately
 * - On change: if field currently has an error, re-validates instantly (instant correction)
 *
 * `task_ref` values must name one of `upstreams`; `multi_select` values must
 * be a non-empty subset of the field's options.
 *
 * Resets automatically when selectedNodeId changes.
 */

import { useCallback, useEffect, useMemo, useReducer, useRef } from "react";

import type { ConfigFieldSchema } from "#/api/generated/model";
import { coerceFieldValue } from "#/lib/config-fields";
import type { UpstreamRef } from "#/stores/editor-store";

interface ValidationState {
  touched: Set<string>;
  hasAttemptedSave: boolean;
  errors: Map<string, string>;
}

type ValidationAction =
  | { type: "BLUR_FIELD"; key: string; error: string | undefined }
  | { type: "CHANGE_FIELD"; key: string; error: string | undefined }
  | { type: "ATTEMPT_SAVE"; errors: Map<string, string> }
  | { type: "RESET" };

function reducer(
  state: ValidationState,
  action: ValidationAction,
): ValidationState {
  switch (action.type) {
    case "BLUR_FIELD": {
      const touched = new Set(state.touched);
      touched.add(action.key);
      const errors = new Map(state.errors);
      if (action.error) {
        errors.set(action.key, action.error);
      } else {
        errors.delete(action.key);
      }
      return { ...state, touched, errors };
    }

    case "CHANGE_FIELD": {
      // Only re-validate if field currently has an error (instant correction)
      if (!state.errors.has(action.key)) return state;
      const errors = new Map(state.errors);
      if (action.error) {
        errors.set(action.key, action.error);
      } else {
        errors.delete(action.key);
      }
      return { ...state, errors };
    }

    case "ATTEMPT_SAVE":
      return { ...state, hasAttemptedSave: true, errors: action.errors };

    case "RESET":
      return {
        touched: new Set<string>(),
        hasAttemptedSave: false,
        errors: new Map<string, string>(),
      };
  }
}

function isBlank(value: unknown): boolean {
  return !value || (typeof value === "string" && !value.trim());
}

function validateField(
  field: ConfigFieldSchema,
  rawValue: unknown,
  upstreamIds: ReadonlySet<string>,
): string | undefined {
  const value = coerceFieldValue(field, rawValue);
  if (field.required) {
    switch (field.field_type) {
      case "string":
      case "select":
      case "task_ref":
        if (isBlank(value)) return `${field.label} is required`;
        break;
      case "multi_select":
        if (!Array.isArray(value) || value.length === 0) {
          return `${field.label} is required`;
        }
        break;
    }
  }

  if (field.field_type === "number" && value != null && value !== "") {
    const num = Number(value);
    if (field.min != null && num < field.min) {
      return `Must be at least ${field.min}`;
    }
    if (field.max != null && num > field.max) {
      return `Must be at most ${field.max}`;
    }
  }

  if (field.field_type === "task_ref" && !isBlank(value)) {
    if (typeof value !== "string" || !upstreamIds.has(value)) {
      return "Must be one of this node's upstream tasks";
    }
  }

  if (field.field_type === "multi_select" && Array.isArray(value)) {
    const allowed = new Set((field.options ?? []).map((o) => o.value));
    const invalid = value.find((v) => typeof v !== "string" || !allowed.has(v));
    if (invalid !== undefined) {
      return `Unknown option: ${String(invalid)}`;
    }
  }

  return undefined;
}

const INITIAL_STATE: ValidationState = {
  touched: new Set<string>(),
  hasAttemptedSave: false,
  errors: new Map<string, string>(),
};

export interface FieldValidation {
  blurField: (key: string) => void;
  changeField: (key: string, value: unknown) => void;
  attemptSave: () => Map<string, string>;
  getError: (key: string) => string | undefined;
  hasErrors: boolean;
}

const NO_UPSTREAMS: readonly UpstreamRef[] = [];

export function useFieldValidation(
  schema: ConfigFieldSchema[],
  config: Record<string, unknown>,
  selectedNodeId: string | null,
  upstreams: readonly UpstreamRef[] = NO_UPSTREAMS,
): FieldValidation {
  const [state, dispatch] = useReducer(reducer, INITIAL_STATE);

  const upstreamIds = useMemo(
    () => new Set(upstreams.map((u) => u.id)),
    [upstreams],
  );

  // Reset on node switch
  const prevNodeIdRef = useRef(selectedNodeId);
  useEffect(() => {
    if (prevNodeIdRef.current !== selectedNodeId) {
      prevNodeIdRef.current = selectedNodeId;
      dispatch({ type: "RESET" });
    }
  }, [selectedNodeId]);

  const fieldMap = useMemo(
    () => new Map(schema.map((f) => [f.key, f])),
    [schema],
  );

  const blurField = useCallback(
    (key: string) => {
      const field = fieldMap.get(key);
      if (!field) return;
      // Only validate on blur if save was attempted or field already errored
      if (!state.hasAttemptedSave && !state.errors.has(key)) return;
      const error = validateField(field, config[key], upstreamIds);
      dispatch({ type: "BLUR_FIELD", key, error });
    },
    [fieldMap, config, upstreamIds, state.hasAttemptedSave, state.errors],
  );

  const changeField = useCallback(
    (key: string, value: unknown) => {
      const field = fieldMap.get(key);
      if (!field) return;
      const error = validateField(field, value, upstreamIds);
      dispatch({ type: "CHANGE_FIELD", key, error });
    },
    [fieldMap, upstreamIds],
  );

  const attemptSave = useCallback(() => {
    const errors = new Map<string, string>();
    for (const field of schema) {
      const error = validateField(field, config[field.key], upstreamIds);
      if (error) errors.set(field.key, error);
    }
    dispatch({ type: "ATTEMPT_SAVE", errors });
    return errors;
  }, [schema, config, upstreamIds]);

  const getError = useCallback(
    (key: string) => state.errors.get(key),
    [state.errors],
  );

  return {
    blurField,
    changeField,
    attemptSave,
    getError,
    hasErrors: state.errors.size > 0,
  };
}
