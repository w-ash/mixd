/**
 * Right sidebar panel for editing a selected node's configuration.
 *
 * Reads the selected node from the editor store, fetches its config schema
 * from the API via useNodeSchemas(), and renders a dynamic form with
 * two-way binding via store.updateNodeConfig().
 */

import { Settings2, X } from "lucide-react";

import type { ConfigFieldSchema } from "@/api/generated/model";
import { NodeTypeBadge } from "@/components/shared/NodeTypeBadge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { useAnimatedPresence } from "@/hooks/useAnimatedPresence";
import { useFieldValidation } from "@/hooks/useFieldValidation";
import { useNodeSchemas } from "@/hooks/useNodeSchemas";
import { formatNodeTypeName, getNodeCategory } from "@/lib/workflow-config";
import { useEditorStore } from "@/stores/editor-store";

function FieldInput({
  field,
  value,
  onChange,
  onBlur,
  hasError,
  fieldId,
  errorId,
}: {
  field: ConfigFieldSchema;
  value: unknown;
  onChange: (key: string, value: unknown) => void;
  onBlur: (key: string) => void;
  hasError: boolean;
  fieldId: string;
  errorId: string;
}) {
  const ariaProps = {
    "aria-invalid": hasError || undefined,
    "aria-describedby": hasError ? errorId : undefined,
  } as const;
  const errorBorder = hasError ? "border-destructive/50" : "";

  switch (field.field_type) {
    case "string":
      return (
        <Input
          id={fieldId}
          className={`h-8 font-mono text-xs ${errorBorder}`}
          placeholder={field.placeholder ?? undefined}
          value={(value as string) ?? ""}
          onChange={(e) => onChange(field.key, e.target.value || undefined)}
          onBlur={() => onBlur(field.key)}
          {...ariaProps}
        />
      );

    case "number":
      return (
        <Input
          id={fieldId}
          type="number"
          className={`h-8 font-mono text-xs ${errorBorder}`}
          placeholder={field.placeholder ?? undefined}
          min={field.min ?? undefined}
          max={field.max ?? undefined}
          value={value != null ? String(value) : ""}
          onChange={(e) => {
            const num = e.target.value ? Number(e.target.value) : undefined;
            onChange(field.key, num);
          }}
          onBlur={() => onBlur(field.key)}
          {...ariaProps}
        />
      );

    case "select":
      return (
        <Select
          value={
            (value as string) ?? (field.default as string | undefined) ?? ""
          }
          onValueChange={(v) => {
            onChange(field.key, v);
            // Validate immediately on change for selects
            onBlur(field.key);
          }}
        >
          <SelectTrigger
            id={fieldId}
            className={`h-8 w-full font-display text-xs ${errorBorder}`}
            {...ariaProps}
          >
            <SelectValue placeholder="Select..." />
          </SelectTrigger>
          <SelectContent>
            {field.options?.map((opt) => (
              <SelectItem key={opt.value} value={opt.value}>
                <span className="font-display text-xs">{opt.label}</span>
                {opt.description && (
                  <span className="block font-body text-[10px] text-text-faint">
                    {opt.description}
                  </span>
                )}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      );

    case "boolean": {
      const checked = value != null ? Boolean(value) : Boolean(field.default);
      return (
        <div className="flex min-h-[44px] items-center">
          <Switch
            id={fieldId}
            checked={checked}
            onCheckedChange={(c) => onChange(field.key, c)}
          />
        </div>
      );
    }

    default:
      return null;
  }
}

function ConfigField({
  field,
  value,
  onChange,
  onBlur,
  error,
  fieldId,
}: {
  field: ConfigFieldSchema;
  value: unknown;
  onChange: (key: string, value: unknown) => void;
  onBlur: (key: string) => void;
  error?: string;
  fieldId: string;
}) {
  const errorId = `${fieldId}-error`;
  const hasError = !!error;

  return (
    <>
      <FieldInput
        field={field}
        value={value}
        onChange={onChange}
        onBlur={onBlur}
        hasError={hasError}
        fieldId={fieldId}
        errorId={errorId}
      />
      {hasError && (
        <p
          id={errorId}
          className="animate-fade-up text-[11px] font-body text-destructive"
        >
          {error}
        </p>
      )}
    </>
  );
}

export function NodeConfigPanel() {
  const node = useEditorStore((s) =>
    s.nodes.find((n) => n.id === s.selectedNodeId),
  );
  const selectedNodeId = useEditorStore((s) => s.selectedNodeId);
  const updateNodeConfig = useEditorStore((s) => s.updateNodeConfig);
  const updateNodeTaskId = useEditorStore((s) => s.updateNodeTaskId);
  const selectNode = useEditorStore((s) => s.selectNode);
  const { getSchema, getNodeDescription } = useNodeSchemas();

  const isOpen = !!selectedNodeId;
  const { shouldRender, ref, state } = useAnimatedPresence(isOpen);

  const nodeType = node ? (node.data.nodeType as string) : "";
  const schema = getSchema(nodeType);

  const validation = useFieldValidation(
    schema,
    node ? (node.data.config as Record<string, unknown>) : {},
    selectedNodeId,
  );

  if (!shouldRender) return null;

  const animationClass =
    state === "open" ? "animate-slide-in-right" : "animate-slide-out-right";

  // When panel is visible but no node selected (closing animation), show empty
  if (!node) {
    return (
      <div
        ref={ref}
        className={`flex w-80 flex-col border-l border-border bg-surface-sunken ${animationClass}`}
      />
    );
  }

  const config = node.data.config as Record<string, unknown>;
  const taskId = node.data.taskId as string;
  const categoryConfig = getNodeCategory(nodeType);
  const nodeDescription = getNodeDescription(nodeType);

  const handleFieldChange = (key: string, value: unknown) => {
    const next = { ...config };
    if (value === undefined || value === "") {
      delete next[key];
    } else {
      next[key] = value;
    }
    updateNodeConfig(node.id, next);
    validation.changeField(key, value);
  };

  const handleFieldBlur = (key: string) => {
    validation.blurField(key);
  };

  const handleTaskIdChange = (newId: string) => {
    const sanitized = newId.replace(/[^a-z0-9_]/g, "");
    if (sanitized && sanitized !== node.id) {
      // Non-reactive read — avoids re-rendering the panel on every node drag
      const currentNodes = useEditorStore.getState().nodes;
      const isDuplicate = currentNodes.some(
        (n) => n.id === sanitized && n.id !== node.id,
      );
      if (!isDuplicate) {
        updateNodeTaskId(node.id, sanitized);
      }
    }
  };

  return (
    <div
      ref={ref}
      className={`flex w-80 flex-col border-l border-border bg-surface-sunken ${animationClass}`}
    >
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <div className="flex items-center gap-2">
          <categoryConfig.Icon
            size={14}
            strokeWidth={1.5}
            style={{ color: categoryConfig.accentColor }}
            aria-hidden="true"
          />
          <NodeTypeBadge nodeType={nodeType} />
        </div>
        <Button
          variant="ghost"
          size="icon-xs"
          onClick={() => selectNode(null)}
          aria-label="Close panel"
        >
          <X size={14} />
        </Button>
      </div>

      {/* Form — keyed by node ID for content crossfade */}
      <div
        key={selectedNodeId}
        className="flex-1 animate-fade-up space-y-4 overflow-y-auto p-4"
      >
        {/* Node label */}
        <p className="font-display text-xs font-medium text-text">
          {formatNodeTypeName(nodeType)}
        </p>

        {/* Task ID */}
        <div className="space-y-1.5">
          <label
            htmlFor="config-task-id"
            className="font-display text-[11px] font-medium uppercase tracking-wider text-text-muted"
          >
            Task ID
          </label>
          <Input
            id="config-task-id"
            className="h-8 font-mono text-xs"
            value={taskId}
            onChange={(e) => handleTaskIdChange(e.target.value)}
          />
        </div>

        {/* Dynamic config fields */}
        {schema.length > 0 && (
          <div
            className="h-px w-full"
            style={{
              backgroundColor: `color-mix(in oklch, ${categoryConfig.accentColor} 20%, transparent)`,
            }}
          />
        )}

        {schema.map((field) => {
          const fieldId = `config-${field.key}`;
          return (
            <div key={field.key} className="space-y-1.5">
              <label
                htmlFor={fieldId}
                className="flex items-baseline gap-1 font-display text-[11px] font-medium uppercase tracking-wider text-text-muted"
              >
                {field.label}
                {field.required && (
                  <span className="text-destructive" title="required">
                    *
                  </span>
                )}
              </label>
              {field.description && (
                <p className="font-body text-[10px] text-text-faint">
                  {field.description}
                </p>
              )}
              <ConfigField
                field={field}
                value={config[field.key]}
                onChange={handleFieldChange}
                onBlur={handleFieldBlur}
                error={validation.getError(field.key)}
                fieldId={fieldId}
              />
            </div>
          );
        })}

        {schema.length === 0 && (
          <div className="flex flex-col items-center gap-3 pt-6 text-center">
            <div className="flex size-10 items-center justify-center rounded-lg bg-surface-elevated">
              <Settings2
                size={18}
                strokeWidth={1.5}
                className="text-text-faint"
              />
            </div>
            <div className="space-y-1">
              <p className="font-display text-xs text-text-muted">
                No configuration needed
              </p>
              {nodeDescription && (
                <p className="max-w-[220px] font-body text-[10px] text-text-faint">
                  {nodeDescription}
                </p>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
